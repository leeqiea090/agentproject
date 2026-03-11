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
    ValidationGate,
)
from app.services.evidence_binder import _compute_evidence_coverage

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
}


def compute_validation_gate(
    sections: list[BidDocumentSection],
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    evidence_bindings: dict[str, list[BidEvidenceBinding]] | None = None,
    target_package_ids: list[str] | None = None,
    mode: DocumentMode = DocumentMode.single_package,
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

    # 4) 表格分类混装检测
    table_mixing = False
    if normalized_reqs:
        for pkg_id, reqs in normalized_reqs.items():
            categories_in_tech_table: set[str] = set()
            for req in reqs:
                categories_in_tech_table.add(req.category.value)
            non_tech = categories_in_tech_table - {
                ClauseCategory.technical_requirement.value,
            }
            if non_tech and len(categories_in_tech_table) > 1:
                table_mixing = True
                logger.warning("表格分类混装：包%s 技术表中出现非技术类条款: %s", pkg_id, non_tech)

    # 5) 半截条目检测
    snippet_truncation_count = 0
    _HALF_CUT_PATTERNS = ("（", "(", "中速（", "低速（", "高灵敏度模式（")
    if normalized_reqs:
        for pkg_reqs in normalized_reqs.values():
            for req in pkg_reqs:
                name = req.param_name or ""
                # 以括号结尾（未闭合）视为截断
                if name.endswith("（") or name.endswith("("):
                    snippet_truncation_count += 1
                # param_name 过短且非数值型
                elif len(name) < 4 and not re.search(r"\d", name):
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


def strip_placeholders_for_external(
    sections: list[BidDocumentSection],
) -> list[BidDocumentSection]:
    """外发稿：将残余占位符替换为安全文案，严禁任何待核实/待补证/占位符状态。"""
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
        # 移除内部草稿水印
        content = re.sub(r"\*\*【内部草稿.*?】\*\*\n*", "", content)
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

    # 3) table_category_mixing_rate
    total_req_count = 0
    mixed_count = 0
    for _pkg_id, reqs in normalized_reqs.items():
        for req in reqs:
            total_req_count += 1
            if req.category != ClauseCategory.technical_requirement:
                mixed_count += 1
    mixing_rate = mixed_count / total_req_count if total_req_count > 0 else 0.0

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

    metrics = RegressionMetrics(
        single_package_focus_score=round(focus_score, 3),
        package_contamination_rate=round(contamination_rate, 3),
        table_category_mixing_rate=round(mixing_rate, 3),
        bid_evidence_coverage=round(evidence_cov, 3),
        placeholder_leakage=round(leakage, 3),
        config_detail_score=round(config_score, 3),
        fact_density_per_page=round(fact_density, 2),
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
