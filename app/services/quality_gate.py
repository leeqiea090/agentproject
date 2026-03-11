"""校验门、回归指标、内容清洗模块"""
from __future__ import annotations

import logging
import re

from app.schemas import (
    BidDocumentSection,
    BidEvidenceBinding,
    ClauseCategory,
    DocumentMode,
    DraftLevel,
    NormalizedRequirement,
    RegressionMetrics,
    TenderDocument,
    ValidationGate,
)
from app.services.evidence_binder import _compute_evidence_coverage
from app.services.requirement_processor import _is_truncated_name

logger = logging.getLogger(__name__)

_PLACEHOLDER_PATTERNS = (
    r"\[待填写\]",
    r"\[投标方公司名称\]",
    r"\[品牌型号\]",
    r"待核实",
    r"待补投标方证据",
    r"待补充",
    r"\[待.*?\]",
)

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

# ── 回归指标质量阈值 ──
_REGRESSION_THRESHOLDS = {
    "single_package_focus_score": 0.8,       # 单包聚焦度应 ≥ 0.8
    "package_contamination_rate": 0.05,       # 污染率应 ≤ 0.05
    "table_category_mixing_rate": 0.1,        # 混表率应 ≤ 0.1
    "bid_evidence_coverage": 0.5,             # 证据覆盖率应 ≥ 0.5
    "placeholder_leakage": 0.1,               # 占位符泄漏应 ≤ 0.1
    "config_detail_score": 0.5,               # 配置详细度应 ≥ 0.5
    "fact_density_per_page": 3.0,             # 每页事实密度应 ≥ 3.0
    "snippet_cleanliness_score": 0.7,         # 片段清洁度应 ≥ 0.7
    "draft_usability_score": 0.5,             # 底稿可用性应 ≥ 0.5
    "project_meta_consistency_score": 0.8,    # 项目名称/编号/数量一致性应 ≥ 0.8
}


def _target_packages(
    tender: TenderDocument | None,
    target_package_ids: list[str] | None,
):
    if tender is None:
        return []
    wanted = {str(pkg_id) for pkg_id in (target_package_ids or []) if str(pkg_id).strip()}
    if not wanted:
        return list(tender.packages)
    return [pkg for pkg in tender.packages if pkg.package_id in wanted]


def _collect_project_meta_issues(
    full_text: str,
    tender: TenderDocument | None,
    target_package_ids: list[str] | None = None,
) -> list[str]:
    """检查项目名称/编号/包件数量是否稳定落正文。"""
    if tender is None or not full_text.strip():
        return []

    issues: list[str] = []
    if tender.project_name and tender.project_name not in full_text:
        issues.append("项目名称未命中")
    if tender.project_number and tender.project_number not in full_text:
        issues.append("项目编号未命中")

    lines = [line.strip() for line in full_text.splitlines() if line.strip()]
    for pkg in _target_packages(tender, target_package_ids):
        item_name = (pkg.item_name or "").strip()
        if not item_name:
            continue
        item_lines = [line for line in lines if item_name in line]
        if not item_lines:
            issues.append(f"包{pkg.package_id} 条目未落正文")
            continue
        quantity = str(pkg.quantity)
        quantity_hit = any(quantity in line for line in item_lines)
        if not quantity_hit:
            issues.append(f"包{pkg.package_id} 数量异常")

    return issues


def _compute_project_meta_consistency_score(
    full_text: str,
    tender: TenderDocument | None,
    target_package_ids: list[str] | None = None,
) -> float:
    if tender is None:
        return 1.0

    checks: list[bool] = []
    if tender.project_name:
        checks.append(tender.project_name in full_text)
    if tender.project_number:
        checks.append(tender.project_number in full_text)
    for pkg in _target_packages(tender, target_package_ids):
        item_name = (pkg.item_name or "").strip()
        if not item_name:
            continue
        item_lines = [line.strip() for line in full_text.splitlines() if item_name in line]
        checks.append(bool(item_lines))
        if item_lines:
            checks.append(any(str(pkg.quantity) in line for line in item_lines))

    if not checks:
        return 1.0
    return sum(1 for ok in checks if ok) / len(checks)


def compute_validation_gate(
    sections: list[BidDocumentSection],
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    evidence_bindings: dict[str, list[BidEvidenceBinding]] | None = None,
    target_package_ids: list[str] | None = None,
    mode: DocumentMode = DocumentMode.single_package,
    tender: TenderDocument | None = None,
) -> ValidationGate:
    """计算硬校验门的 4 个条件。"""
    normalized_reqs = normalized_reqs or {}
    evidence_bindings = evidence_bindings or {}
    target_package_ids = target_package_ids or []

    # 1) 占位符计数
    placeholder_count = 0
    full_text = "\n".join(s.content for s in sections)
    for pattern in _PLACEHOLDER_PATTERNS:
        placeholder_count += len(re.findall(pattern, full_text))

    # 1b) 项目元信息异常检测
    project_meta_issues = _collect_project_meta_issues(full_text, tender, target_package_ids)
    project_meta_anomaly_detected = bool(project_meta_issues)
    if project_meta_anomaly_detected:
        logger.warning("项目元信息异常：%s", "；".join(project_meta_issues[:5]))

    # 2) 包件污染检测 — 单包和多包模式均检测
    package_contamination = False
    if target_package_ids:
        if len(target_package_ids) == 1:
            # 单包模式：检测是否混入其他包内容
            target_pkg = target_package_ids[0]
            for pkg_id in range(1, 20):
                other_id = str(pkg_id)
                if other_id == target_pkg:
                    continue
                pattern = f"包{other_id}[：:]|包\\s*{other_id}\\s*[：:]"
                if re.search(pattern, full_text):
                    sections_excl_quote = [s for s in sections if "报价" not in s.section_title]
                    tech_text = "\n".join(s.content for s in sections_excl_quote)
                    if re.search(pattern, tech_text):
                        package_contamination = True
                        logger.warning("包件污染：单包模式下检出包%s 的内容出现在包%s 技术区域", other_id, target_pkg)
                        break
        else:
            # 多包模式：检测各包章节是否混入非本包内容
            for s in sections:
                section_pkg = ""
                for pid in target_package_ids:
                    if f"包{pid}" in s.section_title:
                        section_pkg = pid
                        break
                if not section_pkg:
                    continue
                for other_pid in target_package_ids:
                    if other_pid == section_pkg:
                        continue
                    pattern = f"包{other_pid}[：:]|包\\s*{other_pid}\\s*[：:]"
                    if re.search(pattern, s.content) and "报价" not in s.section_title:
                        package_contamination = True
                        logger.warning("包件污染：多包模式下包%s 章节混入包%s 内容", section_pkg, other_pid)
                        break
                if package_contamination:
                    break

    # 3) 投标侧证据覆盖率
    all_bindings: list[BidEvidenceBinding] = []
    for pkg_bindings in evidence_bindings.values():
        all_bindings.extend(pkg_bindings)
    evidence_cov = _compute_evidence_coverage(all_bindings)

    # 4) 表格分类混装检测 — 只检查"技术偏离表"，不检查配置明细表
    #    配置明细表合法包含培训/文件/耗材等项，不算混装
    table_mixing = False
    _TECH_TABLE_START = re.compile(r"技术偏离")
    _TECH_TABLE_END = re.compile(r"^#{2,}|^（二）|详细配置明细")
    for section in sections:
        in_tech_table = False
        for line in section.content.splitlines():
            stripped = line.strip()
            if _TECH_TABLE_START.search(stripped):
                in_tech_table = True
                continue
            if in_tech_table and _TECH_TABLE_END.search(stripped):
                in_tech_table = False
            if in_tech_table and stripped.startswith("#"):
                in_tech_table = False
            # 在技术偏离表区域内检测混入的非技术条款关键词
            if in_tech_table and stripped.startswith("|"):
                cells = [c.strip() for c in stripped.split("|") if c.strip()]
                if len(cells) >= 2:
                    req_cell = cells[1] if len(cells) > 1 else ""
                    # 跳过表头/分隔行
                    if req_cell.startswith("---") or req_cell in ("招标要求", "参数项", "条款编号"):
                        continue
                    if any(k in req_cell for k in ("售后", "质保", "维修", "保修", "培训",
                                                     "安装调试", "交付与培训", "说明书", "合格证",
                                                     "技术文件（合格证", "投标文件格式", "正本与副本",
                                                     "配置清单", "配备", "标配", "选配", "装箱",
                                                     "附件", "配件", "随机配件")):
                        table_mixing = True
                        logger.warning("表格分类混装检测：技术表中发现非技术条款行: %s", req_cell[:60])

    # 补充：基于归一化数据的静态混装检测（如果已做好分表，各 category 不应只有技术表）
    if not table_mixing and normalized_reqs:
        for pkg_id, reqs in normalized_reqs.items():
            tech_count = sum(1 for r in reqs if r.category == ClauseCategory.technical_requirement)
            non_noise = [r for r in reqs if r.category != ClauseCategory.noise]
            if non_noise and tech_count == len(non_noise):
                # 所有非噪音条款都被标为技术类 — 可能分类不足
                pass  # 不算混装，只是分类不够细
            elif non_noise:
                # 有多种分类 — 这是正常的，只要分表正确就不算混装
                pass

    # 5) 半截条目检测 — 增强：检查 param_name 和 raw_text
    snippet_truncation_count = 0
    if normalized_reqs:
        for pkg_reqs in normalized_reqs.values():
            for req in pkg_reqs:
                name = req.param_name or ""
                if _is_truncated_name(name):
                    snippet_truncation_count += 1
                # raw_text 以括号截断结尾
                elif req.raw_text and (req.raw_text.rstrip().endswith("（") or req.raw_text.rstrip().endswith("(")):
                    snippet_truncation_count += 1

    # 6) 锚点污染率 — 检测模板/系统标记泄漏
    anchor_pollution_rate = 0.0
    _ANCHOR_POLLUTION_MARKERS = ("{{", "}}", "<!--", "-->", "system:", "assistant:", "debug:", "trace:")
    if full_text:
        anchor_hits = sum(full_text.count(m) for m in _ANCHOR_POLLUTION_MARKERS)
        total_lines = max(full_text.count("\n"), 1)
        anchor_pollution_rate = min(anchor_hits / total_lines, 1.0)

    # 7) 证据页码空白率
    evidence_blank_rate = 0.0
    if all_bindings:
        blank_count = sum(1 for b in all_bindings if not b.file_page and not b.snippet)
        evidence_blank_rate = blank_count / len(all_bindings)

    return ValidationGate(
        project_meta_anomaly_detected=project_meta_anomaly_detected,
        package_contamination_detected=package_contamination,
        placeholder_count=placeholder_count,
        bid_evidence_coverage=evidence_cov,
        table_category_mixing=table_mixing,
        snippet_truncation_count=snippet_truncation_count,
        anchor_pollution_rate=anchor_pollution_rate,
        evidence_blank_rate=evidence_blank_rate,
    )


# ═══════════════════════════════════════════════════════════════════
#  Phase 9: 双输出 — 内部稿 vs 外发稿标注
# ═══════════════════════════════════════════════════════════════════

def annotate_draft_level(
    sections: list[BidDocumentSection],
    draft_level: DraftLevel,
) -> list[BidDocumentSection]:
    """根据稿件等级添加水印/标注。"""
    if draft_level == DraftLevel.external_ready:
        return sections

    # 内部稿：在每个章节内容前加水印
    watermark = "**【内部草稿 — 含待核实/待补证项，不可外发】**\n\n"
    annotated: list[BidDocumentSection] = []
    for s in sections:
        annotated.append(BidDocumentSection(
            section_title=s.section_title,
            content=watermark + s.content,
            attachments=s.attachments,
        ))
    return annotated


def normalize_pending_draft_sections(
    sections: list[BidDocumentSection],
) -> list[BidDocumentSection]:
    """将无法自动补齐的占位内容转成明确的“待补充”提示。"""
    normalized: list[BidDocumentSection] = []
    for s in sections:
        content = s.content
        content = re.sub(r"\[投标方公司名称\]", "待补充（投标人名称）", content)
        content = re.sub(r"\[法定代表人\]", "待补充（法定代表人）", content)
        content = re.sub(r"\[授权代表\]", "待补充（授权代表）", content)
        content = re.sub(r"\[联系电话\]", "待补充（联系电话）", content)
        content = re.sub(r"\[联系地址\]", "待补充（联系地址）", content)
        content = re.sub(r"\[公司注册地址\]", "待补充（公司注册地址）", content)
        content = re.sub(r"\[品牌型号\]", "待补充（品牌型号）", content)
        content = re.sub(r"\[生产厂家\]", "待补充（生产厂家）", content)
        content = re.sub(r"\[品牌\]", "待补充（品牌）", content)
        content = re.sub(r"\[待填写\]", "待补充", content)
        content = re.sub(r"\[待补充\]", "待补充", content)
        content = re.sub(r"\[待[^\]]{1,20}\]", "待补充", content)
        content = re.sub(r"待核实（需填入投标产品实参）", "待补充（投标产品实参）", content)
        content = re.sub(r"待核实（未匹配到已证实产品事实）", "待补充（投标产品实参）", content)
        content = re.sub(r"待补充投标方证据", "待补充（投标方证据）", content)
        content = re.sub(r"待补投标方证据", "待补充（投标方证据）", content)
        content = re.sub(r"投标方证据：未绑定", "投标方证据：待补充", content)
        content = re.sub(r"待定位片段", "待补充（原文定位片段）", content)
        content = re.sub(r"招标原文片段", "待补充（招标原文片段）", content)
        content = re.sub(r"待核实", "待补充", content)
        normalized.append(BidDocumentSection(
            section_title=s.section_title,
            content=content,
            attachments=s.attachments,
        ))
    return normalized


def strip_placeholders_for_external(
    sections: list[BidDocumentSection],
) -> list[BidDocumentSection]:
    """外发稿：严禁任何待核实/待补证/占位符/招标原文片段状态。

    External 不允许：待核实、待补证、占位符、招标原文片段。
    """
    cleaned: list[BidDocumentSection] = []
    for s in sections:
        content = s.content
        content = re.sub(r"\[待填写\]", "（详见附件）", content)
        content = re.sub(r"\[投标方公司名称\]", "（见封面）", content)
        content = re.sub(r"\[品牌型号\]", "（详见技术偏离表）", content)
        content = re.sub(r"待核实（需填入投标产品实参）", "（详见产品技术资料）", content)
        content = re.sub(r"待核实（未匹配到已证实产品事实）", "（详见产品技术资料）", content)
        content = re.sub(r"待补投标方证据", "（详见证据附件）", content)
        content = re.sub(r"待补充投标方证据", "（详见证据附件）", content)
        content = re.sub(r"待补充", "（详见附件）", content)
        content = re.sub(r"待核实", "（详见产品技术资料）", content)
        content = re.sub(r"待定位片段", "（详见原文）", content)
        content = re.sub(r"招标原文片段", "（详见原文）", content)
        # 移除内部草稿水印
        content = re.sub(r"\*\*【内部草稿.*?】\*\*\n*", "", content)
        # 移除任何残留的 [待X] 格式占位符
        content = re.sub(r"\[待[^\]]{1,20}\]", "（详见附件）", content)
        cleaned.append(BidDocumentSection(
            section_title=s.section_title,
            content=content,
            attachments=s.attachments,
        ))
    return cleaned


# ═══════════════════════════════════════════════════════════════════
#  Phase 10: 评测回归指标
# ═══════════════════════════════════════════════════════════════════

def compute_regression_metrics(
    sections: list[BidDocumentSection],
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    evidence_bindings: dict[str, list[BidEvidenceBinding]] | None = None,
    target_package_ids: list[str] | None = None,
    total_pages_estimate: int = 1,
    tender: TenderDocument | None = None,
) -> RegressionMetrics:
    """计算 7 项回归质量指标，并与阈值对比输出告警。"""
    normalized_reqs = normalized_reqs or {}
    evidence_bindings = evidence_bindings or {}
    target_package_ids = target_package_ids or []
    full_text = "\n".join(s.content for s in sections)

    # 1) single_package_focus_score
    if target_package_ids and len(target_package_ids) == 1:
        target_pkg = target_package_ids[0]
        target_mentions = len(re.findall(f"包{target_pkg}", full_text))
        other_mentions = 0
        for i in range(1, 20):
            oid = str(i)
            if oid == target_pkg:
                continue
            other_mentions += len(re.findall(f"包{oid}", full_text))
        total = target_mentions + other_mentions
        focus_score = target_mentions / total if total > 0 else 1.0
    else:
        focus_score = 0.5  # 多包模式不评估聚焦度

    # 2) package_contamination_rate — 支持多包模式
    total_sections = len(sections)
    contaminated = 0
    if target_package_ids and len(target_package_ids) == 1:
        target_pkg = target_package_ids[0]
        for s in sections:
            for i in range(1, 20):
                oid = str(i)
                if oid == target_pkg:
                    continue
                if f"包{oid}" in s.content and "报价" not in s.section_title:
                    contaminated += 1
                    break
    elif target_package_ids and len(target_package_ids) > 1:
        for s in sections:
            section_pkg = ""
            for pid in target_package_ids:
                if f"包{pid}" in s.section_title:
                    section_pkg = pid
                    break
            if not section_pkg or "报价" in s.section_title:
                continue
            for other_pid in target_package_ids:
                if other_pid == section_pkg and f"包{other_pid}" in s.content:
                    continue
                if f"包{other_pid}" in s.content and other_pid != section_pkg:
                    contaminated += 1
                    break
    contamination_rate = contaminated / total_sections if total_sections > 0 else 0.0

    # 3) table_category_mixing_rate — 只扫描"技术偏离表"区域
    total_tech_table_rows = 0
    mixed_rows_in_tech = 0
    _TECH_HDR = re.compile(r"技术偏离")
    _TECH_END = re.compile(r"^#{2,}|^（二）|详细配置明细")
    _SVC_KW = ("售后", "质保", "维修", "保修", "培训", "安装调试", "技术支持", "巡检", "交付与培训")
    _DOC_KW = ("说明书", "合格证", "使用手册", "操作手册", "保修卡", "技术文件（合格证")
    _CMP_KW = ("投标文件格式", "正本与副本", "装订成册", "签字确认")
    for section in sections:
        in_tech = False
        for line in section.content.splitlines():
            s = line.strip()
            if _TECH_HDR.search(s):
                in_tech = True
                continue
            if in_tech and _TECH_END.search(s):
                in_tech = False
            if in_tech and s.startswith("#"):
                in_tech = False
            if in_tech and s.startswith("|") and not s.startswith("|---") and not s.startswith("| 序号") and not s.startswith("| 条款编号"):
                total_tech_table_rows += 1
                cells = [c.strip() for c in s.split("|") if c.strip()]
                req_cell = cells[1] if len(cells) > 1 else ""
                if any(k in req_cell for k in (*_SVC_KW, *_DOC_KW, *_CMP_KW)):
                    mixed_rows_in_tech += 1
    mixing_rate = mixed_rows_in_tech / total_tech_table_rows if total_tech_table_rows > 0 else 0.0

    # 4) bid_evidence_coverage
    all_bindings: list[BidEvidenceBinding] = []
    for pkg_bindings in evidence_bindings.values():
        all_bindings.extend(pkg_bindings)
    evidence_cov = _compute_evidence_coverage(all_bindings)

    # 5) placeholder_leakage
    placeholder_count = 0
    for pattern in _PLACEHOLDER_PATTERNS:
        placeholder_count += len(re.findall(pattern, full_text))
    total_chars = len(full_text)
    # 归一化：每 1000 字符的占位符数
    leakage = min(1.0, placeholder_count / (total_chars / 1000 + 1) * 0.1) if total_chars > 0 else 0.0

    # 6) config_detail_score
    config_reqs = sum(
        1 for reqs in normalized_reqs.values()
        for r in reqs if r.category == ClauseCategory.config_requirement
    )
    config_with_detail = 0
    # 检查配置表中有多少行有实际内容（非占位符）
    config_pattern = re.compile(r"\|\s*\d+\s*\|.*?\|.*?\|.*?\|.*?\|.*?\|")
    config_rows = config_pattern.findall(full_text)
    for row in config_rows:
        if not re.search(r"待填写|待补充|待核实", row):
            config_with_detail += 1
    config_score = min(1.0, config_with_detail / max(config_reqs, 5))

    # 7) fact_density_per_page
    # 统计非占位符的事实陈述数（含数值的行）
    fact_lines = len(re.findall(r"[\d.,]+\s*(?:nm|μm|mm|ml|L|℃|Hz|W|V|%|通道|个|台)", full_text))
    pages = max(1, total_pages_estimate)
    fact_density = fact_lines / pages

    # 8) snippet_cleanliness_score — 原文片段清洁度
    # 检查 source_excerpt / evidence snippet 是否带出后续多条或串邻
    total_snippets = 0
    clean_snippets = 0
    _TRAILING_NOISE = ("；", ";", "\n", "|")
    for _pkg_id, reqs in normalized_reqs.items():
        for req in reqs:
            total_snippets += 1
            raw = req.raw_text or ""
            # 干净 = 不含多余换行、不含未闭合括号、不以分隔符结尾
            is_clean = True
            if raw.count("\n") > 1:
                is_clean = False
            if raw.rstrip().endswith(("（", "(")) and raw.count("（") + raw.count("(") > raw.count("）") + raw.count(")"):
                is_clean = False
            if len(raw) > 200:
                is_clean = False
            if is_clean:
                clean_snippets += 1
    snippet_cleanliness = clean_snippets / total_snippets if total_snippets > 0 else 1.0

    # 9) draft_usability_score — 底稿可用性
    # 综合各指标：有槽位 + 分表清晰 + 不过碎不过厚
    usability_factors = []
    # 技术表/配置表/服务表是否都有内容
    _TABLE_TYPES_EXPECTED = ("技术", "配置", "服务", "售后")
    tables_found = sum(1 for t in _TABLE_TYPES_EXPECTED if t in full_text)
    usability_factors.append(min(1.0, tables_found / 3))
    # 占位符不过多（有一些是正常的，太多不可用）
    usability_factors.append(max(0.0, 1.0 - leakage * 2))
    # 配置详细度
    usability_factors.append(config_score)
    # 证据覆盖
    usability_factors.append(min(1.0, evidence_cov * 1.5))
    # 片段清洁度
    usability_factors.append(snippet_cleanliness)
    draft_usability = sum(usability_factors) / len(usability_factors) if usability_factors else 0.0
    project_meta_consistency = _compute_project_meta_consistency_score(full_text, tender, target_package_ids)

    metrics = RegressionMetrics(
        single_package_focus_score=round(focus_score, 3),
        package_contamination_rate=round(contamination_rate, 3),
        table_category_mixing_rate=round(mixing_rate, 3),
        bid_evidence_coverage=round(evidence_cov, 3),
        placeholder_leakage=round(leakage, 3),
        config_detail_score=round(config_score, 3),
        fact_density_per_page=round(fact_density, 2),
        snippet_cleanliness_score=round(snippet_cleanliness, 3),
        draft_usability_score=round(draft_usability, 3),
        project_meta_consistency_score=round(project_meta_consistency, 3),
    )

    # ── 阈值对比：输出质量告警 ──
    warnings: list[str] = []
    t = _REGRESSION_THRESHOLDS
    if focus_score < t["single_package_focus_score"] and len(target_package_ids) == 1:
        warnings.append(f"单包聚焦度不足: {focus_score:.2f} < {t['single_package_focus_score']}")
    if contamination_rate > t["package_contamination_rate"]:
        warnings.append(f"包件污染率过高: {contamination_rate:.2f} > {t['package_contamination_rate']}")
    if mixing_rate > t["table_category_mixing_rate"]:
        warnings.append(f"表格混装率过高: {mixing_rate:.2f} > {t['table_category_mixing_rate']}")
    if evidence_cov < t["bid_evidence_coverage"]:
        warnings.append(f"证据覆盖率不足: {evidence_cov:.2f} < {t['bid_evidence_coverage']}")
    if leakage > t["placeholder_leakage"]:
        warnings.append(f"占位符泄漏过多: {leakage:.2f} > {t['placeholder_leakage']}")
    if config_score < t["config_detail_score"]:
        warnings.append(f"配置详细度不足: {config_score:.2f} < {t['config_detail_score']}")
    if fact_density < t["fact_density_per_page"]:
        warnings.append(f"每页事实密度不足: {fact_density:.1f} < {t['fact_density_per_page']}")
    if snippet_cleanliness < t["snippet_cleanliness_score"]:
        warnings.append(f"原文片段清洁度不足: {snippet_cleanliness:.2f} < {t['snippet_cleanliness_score']}")
    if draft_usability < t["draft_usability_score"]:
        warnings.append(f"底稿可用性不足: {draft_usability:.2f} < {t['draft_usability_score']}")
    if project_meta_consistency < t["project_meta_consistency_score"]:
        warnings.append(
            f"项目元信息一致性不足: {project_meta_consistency:.2f} < {t['project_meta_consistency_score']}"
        )

    if warnings:
        for w in warnings:
            logger.warning("回归指标告警: %s", w)
        metrics.quality_warnings = warnings
    else:
        logger.info("回归指标: 全部通过质量阈值")

    return metrics


# ── Sanitization ──

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


# ── 自愈：技术表混装清洗 ──
_HEAL_SVC_KW = ("售后", "质保", "维修", "保修", "培训", "安装调试", "技术支持", "巡检", "响应时间", "交付与培训", "质保与售后")
_HEAL_DOC_KW = ("说明书", "合格证", "使用手册", "操作手册", "保修卡", "技术文档", "随机文件", "技术文件（合格证")
_HEAL_CMP_KW = ("投标文件格式", "正本与副本", "装订成册", "签字确认", "页码要求")
_HEAL_TECH_TABLE_HDR = re.compile(r"技术偏离")
_HEAL_TECH_TABLE_END = re.compile(r"^#{2,}|^（二）|详细配置明细")


def _heal_table_mixing(sections: list[BidDocumentSection]) -> list[BidDocumentSection]:
    """从技术偏离表中移除非技术行，将其移到独立的附录分表。

    返回清洗后的 sections（可能多出附录章节）。
    """
    healed: list[BidDocumentSection] = []
    extracted_svc_lines: list[str] = []
    extracted_doc_lines: list[str] = []

    for section in sections:
        new_lines: list[str] = []
        in_tech_table = False
        for line in section.content.splitlines():
            stripped = line.strip()
            if _HEAL_TECH_TABLE_HDR.search(stripped):
                in_tech_table = True
                new_lines.append(line)
                continue
            if in_tech_table and (_HEAL_TECH_TABLE_END.search(stripped) or stripped.startswith("#")):
                in_tech_table = False

            if in_tech_table and stripped.startswith("|") and not stripped.startswith("|---"):
                cells = [c.strip() for c in stripped.split("|") if c.strip()]
                req_cell = cells[1] if len(cells) > 1 else ""
                if any(k in req_cell for k in _HEAL_SVC_KW):
                    extracted_svc_lines.append(stripped)
                    continue  # 不写入原表
                if any(k in req_cell for k in _HEAL_DOC_KW):
                    extracted_doc_lines.append(stripped)
                    continue
                if any(k in req_cell for k in _HEAL_CMP_KW):
                    continue  # 丢弃合规格式类

            new_lines.append(line)

        healed.append(BidDocumentSection(
            section_title=section.section_title,
            content="\n".join(new_lines),
            attachments=section.attachments,
        ))

    # 将提取出的非技术行组成独立分表章节
    if extracted_svc_lines:
        svc_content = "\n".join([
            "### 售后服务/培训要求响应表（自动分离）",
            "| 序号 | 要求内容 | 响应承诺 |",
            "|---:|---|---|",
            *[_renumber_row(i, row) for i, row in enumerate(extracted_svc_lines, 1)],
        ])
        healed.append(BidDocumentSection(
            section_title="售后服务要求响应表",
            content=svc_content,
        ))

    if extracted_doc_lines:
        doc_content = "\n".join([
            "### 资料/文档要求响应表（自动分离）",
            "| 序号 | 要求内容 | 响应承诺 |",
            "|---:|---|---|",
            *[_renumber_row(i, row) for i, row in enumerate(extracted_doc_lines, 1)],
        ])
        healed.append(BidDocumentSection(
            section_title="资料要求响应表",
            content=doc_content,
        ))

    if extracted_svc_lines or extracted_doc_lines:
        logger.info(
            "自愈分离: %d 条售后/服务行, %d 条资料行从技术表移出",
            len(extracted_svc_lines), len(extracted_doc_lines),
        )

    return healed


def _renumber_row(idx: int, row: str) -> str:
    """将 markdown 表格行的第一列替换为新序号。"""
    cells = [c.strip() for c in row.split("|") if c.strip()]
    if cells:
        cells[0] = str(idx)
    return "| " + " | ".join(cells) + " |"


def _heal_package_contamination(
    sections: list[BidDocumentSection],
    target_package_ids: list[str],
) -> list[BidDocumentSection]:
    """文本级包件污染清洗：移除引用非目标包号的行。"""
    if not target_package_ids:
        return sections

    target_set = set(target_package_ids)
    healed: list[BidDocumentSection] = []

    for section in sections:
        # 报价总览表允许出现多包信息
        if "报价" in section.section_title:
            healed.append(section)
            continue

        new_lines: list[str] = []
        removed = 0
        for line in section.content.splitlines():
            # 检查该行是否引用了非目标包
            has_other_pkg = False
            for pkg_num in range(1, 20):
                pkg_id = str(pkg_num)
                if pkg_id in target_set:
                    continue
                if f"包{pkg_id}" in line and f"包{pkg_id}" not in section.section_title:
                    has_other_pkg = True
                    break
            if has_other_pkg and line.strip().startswith("|"):
                removed += 1
                continue
            new_lines.append(line)

        if removed:
            logger.info("包件污染清洗：章节[%s] 移除 %d 行跨包引用", section.section_title, removed)

        healed.append(BidDocumentSection(
            section_title=section.section_title,
            content="\n".join(new_lines),
            attachments=section.attachments,
        ))

    return healed
