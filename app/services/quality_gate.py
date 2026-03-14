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
from collections import Counter
from app.services.requirement_processor import (
    _is_bad_requirement_value,
    _is_truncated_name,
    _package_forbidden_terms,
)

def _looks_like_nontechnical_row_in_tech_table(req_cell: str) -> bool:
    """
    技术偏离表中的“非技术条款”检测：
    - 只拦服务/验收/资料/格式类
    - 放行设备安全性能类技术项（如安全性检测器、安全门锁、剂量率、报警装置）
    """
    text = (req_cell or "").strip()
    if not text:
        return False

    tech_whitelist = (
        "安全性检测器",
        "安全门锁系统",
        "紧急停止",
        "辐射指示",
        "故障报警装置",
        "剂量率",
        "X射线球管",
        "真空度监测",
        "离子泵",
        "连续辐照",
        "水冷却系统",
    )
    if any(tok in text for tok in tech_whitelist):
        return False

    service_hints = (
        "售后", "质保", "维修", "保修", "培训",
        "响应时间", "上门服务", "技术支持", "巡检",
        "备品备件", "升级服务", "使用培训", "工程师培训",
    )
    acceptance_hints = (
        "验收", "试运行", "安装调试", "到货验收",
        "验收报告", "验收方式", "验收标准",
    )
    documentation_hints = (
        "说明书", "合格证", "操作手册", "使用手册",
        "保修卡", "装箱单", "随机文件", "技术文件（合格证",
    )
    compliance_hints = (
        "投标文件格式", "响应文件格式", "正本与副本", "装订成册",
        "签字确认", "页码要求",
    )

    return any(
        tok in text
        for tok in (*service_hints, *acceptance_hints, *documentation_hints, *compliance_hints)
    )



logger = logging.getLogger(__name__)

_BAD_NAME_SUFFIXES = ("（", "(", "为", "可", "单机", "至少", "最低", "最高")

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

def _detect_procurement_mode_from_text(full_text: str | None, tender=None) -> str:
    text = " ".join(
        [
            full_text or "",
            str(getattr(tender, "project_number", "") or ""),
            str(getattr(tender, "procurement_type", "") or ""),
            " ".join(getattr(tender, "response_section_titles", []) or []),
        ]
    )

    if "[TP]" in text or "竞争性谈判文件" in text or "采购方式 竞争性谈判" in text or "竞争性谈判" in text:
        return "tp"
    if "[CS]" in text or "竞争性磋商文件" in text or "采购方式 竞争性磋商" in text or "竞争性磋商" in text:
        return "cs"
    if "[ZB]" in text or "公开招标" in text or ("招标" in text and "谈判" not in text and "磋商" not in text):
        return "zb"

    return "unknown"

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

def _section_title_text(section: BidDocumentSection) -> str:
    return (getattr(section, "section_title", "") or "").strip()


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
    """
    只检查项目名称、项目编号、包件名称是否落正文。
    当前黑龙江谈判文件首页“谈判内容”中的数量与各包技术明细中的设备总台数可能不一致，
    数量不再作为硬拦截项，避免误报。
    """
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

    if not checks:
        return 1.0
    return sum(1 for ok in checks if ok) / len(checks)

def _req_value_text(req: NormalizedRequirement) -> str:
    raw = (req.raw_text or "").strip()
    if "：" in raw:
        return raw.split("：", 1)[1].strip()
    bits = [str(req.threshold or "").strip(), str(req.unit or "").strip()]
    return "".join(bits).strip()


def _count_bad_requirement_values(
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None,
) -> int:
    if not normalized_reqs:
        return 0

    count = 0
    for reqs in normalized_reqs.values():
        for req in reqs:
            val = _req_value_text(req)
            if val and _is_bad_requirement_value(val):
                count += 1
    return count


def _count_duplicate_requirement_keys(
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None,
) -> int:
    if not normalized_reqs:
        return 0

    dup_groups = 0
    for reqs in normalized_reqs.values():
        keys = [
            (req.param_name or "").strip()
            for req in reqs
            if (req.param_name or "").strip()
        ]
        counter = Counter(keys)
        dup_groups += sum(1 for _, n in counter.items() if n > 1)
    return dup_groups


def _count_rendered_forbidden_hits(
    sections: list[BidDocumentSection],
    tender: TenderDocument | None,
) -> int:
    if tender is None:
        return 0

    package_name_map = {pkg.package_id: pkg.item_name for pkg in tender.packages}
    hit_count = 0

    for section in sections:
        content = section.content or ""
        if not content:
            continue

        # 只扫描第三章技术正文，跳过第四章附件/报价附件
        title = _section_title_text(section)
        if title and ("第四章" in title or "报价书附件" in title):
            continue
        if "第四章 报价书附件" in content:
            content = content.split("第四章 报价书附件", 1)[0]

        for pkg in tender.packages:
            other_names = [
                name for other_id, name in package_name_map.items()
                if other_id != pkg.package_id
            ]
            forbidden = tuple(_package_forbidden_terms(pkg.item_name, other_names))
            if not forbidden:
                continue

            block_pattern = re.compile(
                rf"###\s*包{re.escape(pkg.package_id)}[^\n]*\n(.*?)(?=\n###\s*包\d+[：:]|\n#|\n##|\n###\s*第[一二三四五六七八九十\d]+|\Z)",
                re.S,
            )
            for match in block_pattern.finditer(content):
                block = match.group(1)
                cleaned_block = _clean_rendered_block_for_forbidden_scan(block, tender)
                if any(tok in cleaned_block for tok in forbidden):
                    hit_count += 1

    return hit_count

def _collect_duplicate_requirement_keys(
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not normalized_reqs:
        return result

    for pkg_id, reqs in normalized_reqs.items():
        keys = [(req.param_name or "").strip() for req in reqs if (req.param_name or "").strip()]
        counter = Counter(keys)
        dups = [k for k, n in counter.items() if n > 1]
        if dups:
            result[pkg_id] = dups
    return result





def compute_validation_gate(
    sections: list[BidDocumentSection],
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    evidence_bindings: dict[str, list[BidEvidenceBinding]] | None = None,
    target_package_ids: list[str] | None = None,
    mode: DocumentMode = DocumentMode.single_package,
    tender: TenderDocument | None = None,
    full_text: str | None = None,
) -> ValidationGate:
    """计算硬校验门。"""
    normalized_reqs = normalized_reqs or {}
    evidence_bindings = evidence_bindings or {}
    target_package_ids = target_package_ids or []

    if full_text is None:
        full_text = "\n\n".join(
            f"{(getattr(s, 'section_title', '') or '').strip()}\n{(getattr(s, 'content', '') or '').strip()}".strip()
            for s in (sections or [])
            if (getattr(s, "section_title", "") or "").strip() or (getattr(s, "content", "") or "").strip()
        )

    missing_new = _check_required_new_structure(full_text, tender=tender)
    found_old = _check_forbidden_old_structure(full_text, tender=tender)
    project_meta_issues = _collect_project_meta_issues(full_text, tender, target_package_ids)

    if missing_new:
        logger.warning("新结构缺失: %s", "；".join(missing_new))
    if found_old:
        logger.warning("检测到旧结构残留: %s", "；".join(found_old))
    if project_meta_issues:
        logger.warning("项目元信息异常：%s", "；".join(project_meta_issues[:5]))

    project_meta_anomaly_detected = bool(missing_new or found_old or project_meta_issues)

    dup_detail = _collect_duplicate_requirement_keys(normalized_reqs)
    if dup_detail:
        logger.warning("重复参数键明细: %s", dup_detail)

    forbidden_detail = _collect_rendered_forbidden_hits(sections, tender)
    if forbidden_detail:
        logger.warning("渲染后术语污染明细: %s", forbidden_detail)

    placeholder_count = 0
    for pattern in _PLACEHOLDER_PATTERNS:
        placeholder_count += len(re.findall(pattern, full_text))

    core_fallback_markers = (
        "待补：未从采购文件中自动拆出逐条技术参数，请按采购文件技术要求逐条补录。",
        "详见采购文件技术要求",
        "按采购文件售后服务要求执行。",
    )
    if any(marker in full_text for marker in core_fallback_markers):
        project_meta_anomaly_detected = True
        logger.warning("核心章节仍存在泛化兜底文本，禁止作为可外发底稿。")

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
            if stripped.startswith("#") and _TECH_TABLE_START.search(stripped):
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
                    if _looks_like_nontechnical_row_in_tech_table(req_cell):
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

    # 8) 嵌套占位符检测
    _NESTED_PLACEHOLDER_PATTERNS = [
        r"待补充（待补充",
        r"待补充\(待补充",
        r"（详见[^）]*）（[^）]*）",
        r"待补充（（",
    ]
    nested_placeholder_detected = False
    for np_pat in _NESTED_PLACEHOLDER_PATTERNS:
        if re.search(np_pat, full_text):
            nested_placeholder_detected = True
            logger.warning("嵌套占位符检测：发现嵌套模式 %s", np_pat)
            break

    forbidden_terms_by_package: dict[str, tuple[str, ...]] = {}
    if tender is not None:
        package_name_map = {pkg.package_id: pkg.item_name for pkg in tender.packages}
        for pkg in tender.packages:
            other_names = [
                name for other_id, name in package_name_map.items()
                if other_id != pkg.package_id
            ]
            forbidden_terms_by_package[pkg.package_id] = tuple(
                _package_forbidden_terms(pkg.item_name, other_names)
            )

    # 9) 证据片段不洁净率 — 检测跨包文本和噪音标记残留
    snippet_dirty_rate = 0.0
    if evidence_bindings:
        _dirty_markers = ("评分标准", "投标人须知", "合同条款", "响应文件格式")
        dirty_count = 0
        total_bind_count = 0
        for pkg_id, pkg_bindings in evidence_bindings.items():
            for b in pkg_bindings:
                total_bind_count += 1
                snip = getattr(b, "snippet", "") or getattr(b, "evidence_snippet", "") or ""
                if not snip:
                    continue
                # 检测跨包引用（提到其他包号）
                other_pkg_refs = re.findall(r"包(\d+)", snip)
                if other_pkg_refs and any(ref != pkg_id for ref in other_pkg_refs):
                    dirty_count += 1
                    continue
                # 检测噪音标记残留
                if any(m in snip for m in _dirty_markers):
                    dirty_count += 1
                    continue
                # 检测嵌套占位符
                if "待补充（待补充" in snip or "待补充(待补充" in snip:
                    dirty_count += 1
                    continue
                forbidden = forbidden_terms_by_package.get(pkg_id, ())
                if forbidden and any(t in snip for t in forbidden):
                    dirty_count += 1
                    continue
        if total_bind_count > 0:
            snippet_dirty_rate = dirty_count / total_bind_count

    # 10) 设备术语污染检测 — 如果当前包 requirement 命中禁止词
    device_contamination_count = 0
    if normalized_reqs:
        for pkg_id, reqs in normalized_reqs.items():
            forbidden = forbidden_terms_by_package.get(pkg_id, ())
            if not forbidden:
                continue
            for req in reqs:
                raw = req.raw_text or ""
                if any(t in raw for t in forbidden):
                    device_contamination_count += 1
                    logger.warning("设备术语污染: 包%s req=%s 命中禁止词", pkg_id, req.param_name[:40])
    if device_contamination_count > 0 and not package_contamination:
        package_contamination = True
        logger.warning("设备术语污染触发包件污染标记: %d 条", device_contamination_count)

    # 11) 半截字段后缀检测 — param_name 以 _BAD_NAME_SUFFIXES 结尾
    bad_name_count = 0
    if normalized_reqs:
        for pkg_reqs in normalized_reqs.values():
            for req in pkg_reqs:
                name = (req.param_name or "").strip()
                if name and name.endswith(_BAD_NAME_SUFFIXES):
                    bad_name_count += 1
    if bad_name_count > 0:
        snippet_truncation_count += bad_name_count
        logger.warning("半截字段后缀检测: %d 条参数名以悬空后缀结尾", bad_name_count)

    # 11b) 半截字段值检测
    bad_value_count = _count_bad_requirement_values(normalized_reqs)
    if bad_value_count > 0:
        snippet_truncation_count += bad_value_count
        logger.warning("半截字段值检测: %d 条需求值疑似残缺", bad_value_count)

    # 11c) 重复参数键检测
    duplicate_key_count = _count_duplicate_requirement_keys(normalized_reqs)
    if duplicate_key_count > 0:
        table_mixing = True
        logger.warning("重复参数键检测: %d 组参数键重复，视为表格混装/标签退化", duplicate_key_count)

    # 11d) 渲染后术语污染检测
    rendered_forbidden_hits = _count_rendered_forbidden_hits(sections, tender)
    if rendered_forbidden_hits > 0:
        package_contamination = True
        logger.warning("渲染后术语污染: %d 处包内内容命中禁止词", rendered_forbidden_hits)




    # 12) 项目元信息数量一致性
    if tender and target_package_ids:
        for pkg in _target_packages(tender, target_package_ids):
            qty_str = str(pkg.quantity)
            qty_hits = [line for line in full_text.splitlines() if qty_str in line and pkg.item_name in line]
            if not qty_hits:
                project_meta_anomaly_detected = True
                logger.warning("数量一致性异常: 包%s 数量%s 未在正文中与条目名同行出现", pkg.package_id, qty_str)

    return ValidationGate(
        project_meta_anomaly_detected=project_meta_anomaly_detected,
        package_contamination_detected=package_contamination,
        placeholder_count=placeholder_count,
        bid_evidence_coverage=evidence_cov,
        table_category_mixing=table_mixing,
        snippet_truncation_count=snippet_truncation_count,
        anchor_pollution_rate=anchor_pollution_rate,
        evidence_blank_rate=evidence_blank_rate,
        nested_placeholder_detected=nested_placeholder_detected,
        snippet_dirty_rate=snippet_dirty_rate,
    )


# ═══════════════════════════════════════════════════════════════════
#  Phase 9: 双输出 — 内部稿 vs 外发稿标注
# ═══════════════════════════════════════════════════════════════════

def annotate_draft_level(
    sections: list[BidDocumentSection],
    draft_level: DraftLevel,
) -> list[BidDocumentSection]:
    """根据稿件等级渲染可编辑底稿。
    internal_draft 允许占位符，但默认不再把“内部草稿”横幅写进正文首段，
    以免人工审核时忘删。
    """
    if draft_level == DraftLevel.external_ready:
        return sections

    return render_editable_draft_sections(sections, add_draft_watermark=False)


def _flatten_nested_placeholders(text: str) -> str:
    """消除占位符嵌套，如 '待补充（待补充（投标方证据））' → '待补充（投标方证据）'。"""
    for _ in range(5):
        prev = text
        # 待补充（待补充（X）） → 待补充（X）
        text = re.sub(r"待补充（待补充（([^）]*)））", r"待补充（\1）", text)
        text = re.sub(r"待补充\(待补充\(([^)]*)\)\)", r"待补充（\1）", text)
        # （详见X）（Y） → （详见X）
        text = re.sub(r"（详见[^）]*）（[^）]*）", "（详见附件）", text)
        # 待补充（（X）） → 待补充（X）
        text = re.sub(r"待补充（（([^）]*)））", r"待补充（\1）", text)
        if text == prev:
            break
    return text


def _editable_placeholder(label: str, prefix: str = "待填写") -> str:
    normalized = re.sub(r"\s+", "", (label or "").strip("：:;；，,。 "))
    return f"【{prefix}：{normalized}】" if normalized else f"【{prefix}】"


def _pending_placeholder_repl(match: re.Match[str]) -> str:
    label = (match.group(1) or "").strip()
    if not label:
        return "【待填写】"
    if any(token in label for token in ("证据", "截图", "彩页", "证书", "报告", "材料", "证件", "复印件")):
        prefix = "待补证" if "证据" in label else "待上传"
    elif any(token in label for token in ("原文", "片段", "定位")):
        prefix = "待定位"
    elif any(token in label for token in ("核实", "确认")):
        prefix = "待确认"
    else:
        prefix = "待填写"
    return _editable_placeholder(label, prefix)


def _todo_placeholder_repl(match: re.Match[str]) -> str:
    label = (match.group(1) or "").strip()
    return _editable_placeholder(label, "待填写")


def _render_editable_draft_content(content: str) -> str:
    text = content.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\*\*【内部草稿.*?】\*\*\n*", "", text)
    text = re.sub(r"\*\*【待补充底稿.*?】\*\*\n*", "", text)
    text = _flatten_nested_placeholders(text)

    text = re.sub(r"\[TODO:待补([^\]]*)\]", _todo_placeholder_repl, text)
    text = re.sub(r"\[TODO:待核实([^\]]*)\]", lambda m: _editable_placeholder((m.group(1) or "").strip(), "待确认"), text)
    text = re.sub(r"\[TODO:[^\]]*\]", "【待填写】", text)

    explicit_mappings = {
        "[投标方公司名称]": _editable_placeholder("投标人名称"),
        "[法定代表人]": _editable_placeholder("法定代表人"),
        "[授权代表]": _editable_placeholder("授权代表"),
        "[联系电话]": _editable_placeholder("联系电话"),
        "[联系地址]": _editable_placeholder("联系地址"),
        "[公司注册地址]": _editable_placeholder("公司注册地址"),
        "[品牌型号]": _editable_placeholder("品牌型号"),
        "[生产厂家]": _editable_placeholder("生产厂家"),
        "[品牌]": _editable_placeholder("品牌"),
        "[待填写]": "【待填写】",
        "[待补充]": "【待填写】",
        "待核实（需填入投标产品实参）": _editable_placeholder("投标产品实参"),
        "待核实（未匹配到已证实产品事实）": _editable_placeholder("投标产品实参"),
        "待补充投标方证据": _editable_placeholder("投标方证据", "待补证"),
        "待补投标方证据": _editable_placeholder("投标方证据", "待补证"),
        "投标方证据待补充": _editable_placeholder("投标方证据", "待补证"),
        "投标方证据：未绑定": "投标方证据：【待补证】",
        "待定位片段": _editable_placeholder("招标原文片段", "待定位"),
    }
    for marker, replacement in explicit_mappings.items():
        text = text.replace(marker, replacement)

    text = re.sub(r"（此处留空，待上传([^）]+)）", lambda m: _editable_placeholder((m.group(1) or "").strip(), "待上传"), text)
    text = re.sub(r"\(此处留空，待上传([^)]*)\)", lambda m: _editable_placeholder((m.group(1) or "").strip(), "待上传"), text)
    text = re.sub(r"（此处留空，待按([^）]+)）", lambda m: _editable_placeholder(f"按{(m.group(1) or '').strip()}", "待填写"), text)
    text = re.sub(r"\(此处留空，待按([^)]*)\)", lambda m: _editable_placeholder(f"按{(m.group(1) or '').strip()}", "待填写"), text)

    text = re.sub(r"待补充（([^）]{1,40})）", _pending_placeholder_repl, text)
    text = re.sub(r"待补充\(([^)]{1,40})\)", _pending_placeholder_repl, text)
    text = re.sub(r"待核实（([^）]{1,40})）", lambda m: _editable_placeholder((m.group(1) or "").strip(), "待确认"), text)
    text = re.sub(r"待核实\(([^)]{1,40})\)", lambda m: _editable_placeholder((m.group(1) or "").strip(), "待确认"), text)
    text = re.sub(r"(?<!【)待补充(?![（(【])", "【待填写】", text)
    text = re.sub(r"(?<!【)待核实(?![（(【])", "【待确认】", text)

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def render_editable_draft_sections(
    sections: list[BidDocumentSection],
    *,
    add_draft_watermark: bool | None = None,
) -> list[BidDocumentSection]:
    """将内部待补稿渲染成适合人工直接编辑的 Markdown。"""
    if add_draft_watermark is None:
        add_draft_watermark = any(
            "【内部草稿" in section.content or "【待补充底稿" in section.content
            for section in sections
        )

    watermark = "**【内部草稿（可编辑底稿）：含待填写/待补证项，补齐后再外发】**\n\n"
    rendered: list[BidDocumentSection] = []
    for idx, section in enumerate(sections):
        content = _render_editable_draft_content(section.content)
        if add_draft_watermark and idx == 0:
            content = watermark + content
        rendered.append(section.model_copy(update={"content": content}))
    return rendered


def normalize_pending_draft_sections(
    sections: list[BidDocumentSection],
) -> list[BidDocumentSection]:
    """将无法自动补齐的占位内容转成明确的"待补充"提示。

    规则：先处理带括号的长模式，再处理裸模式，最后展平嵌套。
    """
    normalized: list[BidDocumentSection] = []
    for s in sections:
        content = s.content
        # ── 阶段 1：方括号占位符 → 待补充（说明）──
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

        # ── 阶段 2：带括号的特定模式 → 待补充（说明）──
        content = re.sub(r"待核实（需填入投标产品实参）", "待补充（投标产品实参）", content)
        content = re.sub(r"待核实（未匹配到已证实产品事实）", "待补充（投标产品实参）", content)
        content = re.sub(r"待补充投标方证据", "待补充（投标方证据）", content)
        content = re.sub(r"待补投标方证据", "待补充（投标方证据）", content)
        content = re.sub(r"投标方证据待补充", "待补充（投标方证据）", content)
        content = re.sub(r"投标方证据：未绑定", "投标方证据：待补充", content)
        content = re.sub(r"待定位片段", "待补充（原文定位片段）", content)
        content = re.sub(r"招标原文片段", "待补充（招标原文片段）", content)

        # ── 阶段 3：裸 catch-all（只匹配不带括号的裸"待核实"）──
        content = re.sub(r"待核实(?!（)", "待补充", content)

        # ── 阶段 4：展平嵌套 ──
        content = _flatten_nested_placeholders(content)

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
    规则：先展平嵌套，再替换带括号的长模式，最后 catch-all 裸模式。
    """
    cleaned: list[BidDocumentSection] = []
    for s in sections:
        content = s.content

        # ── 先展平嵌套占位符 ──
        content = _flatten_nested_placeholders(content)

        # ── 阶段 1：方括号占位符 ──
        content = re.sub(r"\[投标方公司名称\]", "（见封面）", content)
        content = re.sub(r"\[品牌型号\]", "（详见技术偏离表）", content)
        content = re.sub(r"\[待填写\]", "（详见附件）", content)
        content = re.sub(r"\[待[^\]]{1,20}\]", "（详见附件）", content)

        # ── 阶段 2：带括号的完整模式（必须在裸 catch-all 之前）──
        content = re.sub(r"待核实（需填入投标产品实参）", "（详见产品技术资料）", content)
        content = re.sub(r"待核实（未匹配到已证实产品事实）", "（详见产品技术资料）", content)
        content = re.sub(r"待补充（[^）]{0,30}）", "（详见附件）", content)
        content = re.sub(r"待补投标方证据", "（详见证据附件）", content)
        content = re.sub(r"待补充投标方证据", "（详见证据附件）", content)
        content = re.sub(r"投标方证据待补充", "（详见证据附件）", content)

        # ── 阶段 3：裸 catch-all ──
        content = re.sub(r"待补充", "（详见附件）", content)
        content = re.sub(r"待核实", "（详见产品技术资料）", content)
        content = re.sub(r"待定位片段", "（详见原文）", content)
        content = re.sub(r"招标原文片段", "（详见原文）", content)

        # ── 阶段 4：移除内部草稿水印 ──
        content = re.sub(r"\*\*【内部草稿.*?】\*\*\n*", "", content)
        content = re.sub(r"\*\*【待补充底稿.*?】\*\*\n*", "", content)

        # ── 阶段 5：展平可能的残留嵌套 ──
        content = _flatten_nested_placeholders(content)

        cleaned.append(BidDocumentSection(
            section_title=s.section_title,
            content=content,
            attachments=s.attachments,
        ))
    return cleaned


_EXTERNAL_REFERENCE_PATTERNS = re.compile(
    r"（详见[^）]{0,20}）|（见[^）]{0,10}）"
)


def check_external_content_density(sections: list[BidDocumentSection]) -> float:
    """检查外发稿中实际内容占比（排除"详见..."引用）。

    返回实质内容字符占总字符的比例（0~1）。
    如果比例低于阈值（如 0.5），说明外发稿大部分是空引用，不应放行。
    """
    total_chars = 0
    reference_chars = 0
    for s in sections:
        content = s.content
        total_chars += len(content)
        for m in _EXTERNAL_REFERENCE_PATTERNS.finditer(content):
            reference_chars += len(m.group())
    if total_chars == 0:
        return 1.0
    return 1.0 - (reference_chars / total_chars)


# ═══════════════════════════════════════════════════════════════════
#  Phase 10: 评测回归指标
# ═══════════════════════════════════════════════════════════════════

def _is_truncated_field(text: str) -> bool:
    """快速检测字段是否截断（用于评测指标，不用于主表过滤）。"""
    if not text or not text.strip():
        return True
    s = text.strip()
    # 以冒号/介词结尾
    if re.search(r"[：:为]$", s):
        return True
    # 括号未闭合
    if s.count("（") + s.count("(") > s.count("）") + s.count(")"):
        return True
    # 以限定词结尾但无数值
    if re.search(r"(至少|最低|最高|不低于|不少于)$", s):
        return True
    return False


def _check_required_new_structure(full_text: str | None, tender=None) -> list[str]:
    text = full_text or ""

    tender_mode = _detect_procurement_mode_from_text("", tender=tender)
    exact_titles = [str(x).strip() for x in (getattr(tender, "response_section_titles", []) or []) if str(x).strip()]
    if tender_mode == "zb" and exact_titles:
        return [x for x in exact_titles if x not in text]

    mode = _detect_procurement_mode_from_text(text, tender=tender)

    if mode == "tp":
        required = [
            "一、响应文件封面格式",
            "二、报价书",
            "三、报价一览表",
            "四、资格承诺函",
            "五、技术偏离及详细配置明细表",
            "六、技术服务和售后服务的内容及措施",
            "七、法定代表人/单位负责人授权书",
            "八、法定代表人/单位负责人和授权代表身份证明",
            "九、小微企业声明函",
            "十、残疾人福利性单位声明函",
            "十一、投标人关联单位的说明",
        ]
    elif mode == "cs":
        required = [
            "一、响应文件封面格式",
            "二、首轮报价表",
            "三、分项报价表",
            "四、技术偏离及详细配置明细表",
            "五、技术服务和售后服务的内容及措施",
            "六、法定代表人/单位负责人授权书",
            "七、法定代表人/单位负责人和授权代表身份证明",
            "八、小微企业声明函",
            "九、残疾人福利性单位声明函",
            "十、投标人关联单位的说明",
            "十一、资格承诺函",
        ]
    else:
        return []

    return [x for x in required if x not in text]


def _check_forbidden_old_structure(full_text: str | None, tender=None) -> list[str]:
    text = full_text or ""

    tender_mode = _detect_procurement_mode_from_text("", tender=tender)
    exact_titles = [str(x).strip() for x in (getattr(tender, "response_section_titles", []) or []) if str(x).strip()]
    if tender_mode == "zb" and exact_titles:
        # 对“招标文件/第六章原格式驱动”的项目，不再做 TP/CS 老结构误杀
        return []

    mode = _detect_procurement_mode_from_text(text, tender=tender)

    if mode == "tp":
        forbidden = [
            "一、封面格式",
            "二、首轮报价表",
            "三、分项报价表",
            "七、资格性审查响应对照表",
            "八、符合性审查响应对照表",
            "九、投标无效情形汇总及自检表",
            "七、报价书附件",
            "竞争性磋商文件",
        ]
    elif mode == "cs":
        forbidden = [
            "一、封面格式",
            "二、报价书",
            "三、报价一览表",
            "四、资格承诺函",
            "五、详细配置明细",
            "六、技术偏离表",
            "七、报价书附件",
            "竞争性谈判文件",
        ]
    else:
        return []

    return [x for x in forbidden if x in text]

def _clean_rendered_block_for_forbidden_scan(
    block: str,
    tender: TenderDocument | None = None,
) -> str:
    """
    对渲染后的包内区块做降噪，避免把项目名称/项目编号/数量/交货期等
    元信息误判为“跨包术语污染”。
    """
    if not block:
        return ""

    drop_prefixes = (
        "项目名称：",
        "项目编号：",
        "数量：",
        "交货期：",
        "交货地点：",
        "供应商全称：",
        "日期：",
        "说明：",
    )

    kept: list[str] = []
    for raw in block.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(drop_prefixes):
            continue
        if s.startswith("### 包") or s.startswith("#### "):
            continue
        kept.append(s)

    cleaned = "\n".join(kept)

    # 项目名称经常包含多个包名，直接整串剔除，避免误判
    project_name = (getattr(tender, "project_name", "") or "").strip()
    if project_name:
        cleaned = cleaned.replace(project_name, "")

    return cleaned





def _collect_rendered_forbidden_hits(
    sections: list[BidDocumentSection],
    tender: TenderDocument | None,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if tender is None:
        return result

    package_name_map = {pkg.package_id: pkg.item_name for pkg in tender.packages}

    for section in sections:
        content = section.content or ""
        if not content:
            continue

        title = _section_title_text(section)
        if title and ("报价书附件" in title):
            continue
        if "报价书附件" in content:
            content = content.split("报价书附件", 1)[0]

        for pkg in tender.packages:
            other_names = [
                name for other_id, name in package_name_map.items()
                if other_id != pkg.package_id
            ]
            forbidden = tuple(_package_forbidden_terms(pkg.item_name, other_names))
            if not forbidden:
                continue

            block_pattern = re.compile(
                rf"###\s*包{re.escape(pkg.package_id)}[^\n]*\n(.*?)(?=\n###\s*包\d+[：:]|\n#|\n##|\n###\s*第[一二三四五六七八九十\d]+|\Z)",
                re.S,
            )

            hits: list[str] = []
            for match in block_pattern.finditer(content):
                block = match.group(1)
                cleaned_block = _clean_rendered_block_for_forbidden_scan(block, tender)
                hits.extend(tok for tok in forbidden if tok in cleaned_block)

            if hits:
                result.setdefault(pkg.package_id, [])
                result[pkg.package_id].extend(hits)

    return {k: sorted(set(v)) for k, v in result.items()}

def compute_regression_metrics(
    sections: list[BidDocumentSection],
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    evidence_bindings: dict[str, list[BidEvidenceBinding]] | None = None,
    target_package_ids: list[str] | None = None,
    total_pages_estimate: int = 1,
    tender: TenderDocument | None = None,
    workflow_stage: str = "evidence_ready",
) -> RegressionMetrics:
    """计算 7 项回归质量指标，并与阈值对比输出告警。"""
    normalized_reqs = normalized_reqs or {}
    evidence_bindings = evidence_bindings or {}
    target_package_ids = target_package_ids or []
    full_text = "\n\n".join(
        f"{(getattr(s, 'section_title', '') or '').strip()}\n{(getattr(s, 'content', '') or '').strip()}".strip()
        for s in (sections or [])
        if (getattr(s, "section_title", "") or "").strip() or (getattr(s, "content", "") or "").strip()
    )

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
        focus_score = 0.5

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
            if s.startswith("#") and _TECH_HDR.search(s):
                in_tech = True
                continue
            if in_tech and _TECH_END.search(s):
                in_tech = False
            if in_tech and s.startswith("#"):
                in_tech = False
            if in_tech and s.startswith("|") and not s.startswith("|---") and not s.startswith(
                    "| 序号") and not s.startswith("| 条款编号"):
                total_tech_table_rows += 1
                cells = [c.strip() for c in s.split("|") if c.strip()]
                req_cell = cells[1] if len(cells) > 1 else ""
                if _looks_like_nontechnical_row_in_tech_table(req_cell):
                    mixed_rows_in_tech += 1
    mixing_rate = mixed_rows_in_tech / total_tech_table_rows if total_tech_table_rows > 0 else 0.0

    all_bindings: list[BidEvidenceBinding] = []
    for pkg_bindings in evidence_bindings.values():
        all_bindings.extend(pkg_bindings)
    evidence_cov = _compute_evidence_coverage(all_bindings)

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

    # 统计非占位符的事实陈述数（含数值的行）
    fact_lines = len(re.findall(r"[\d.,]+\s*(?:nm|μm|mm|ml|L|℃|Hz|W|V|%|通道|个|台)", full_text))
    pages = max(1, total_pages_estimate)
    fact_density = fact_lines / pages

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

    # 综合评估：内容密度、分表完整度、截断率、跨包污染
    usability_factors = []
    # 技术表/配置表/服务表是否都有实质内容（不仅仅是标题）
    _TABLE_TYPES_EXPECTED = ("技术", "配置", "服务", "售后")
    tables_with_content = 0
    for t in _TABLE_TYPES_EXPECTED:
        # 检查关键词后是否有至少 50 字的实质内容
        idx = full_text.find(t)
        if idx >= 0 and len(full_text[idx:idx+200].strip()) > 50:
            tables_with_content += 1
    usability_factors.append(min(1.0, tables_with_content / 3))
    # 占位符不过多（有一些是正常的，太多不可用）
    usability_factors.append(max(0.0, 1.0 - leakage * 2))
    # 配置详细度
    usability_factors.append(config_score)
    # 证据覆盖
    usability_factors.append(min(1.0, evidence_cov * 1.5))
    # 片段清洁度
    usability_factors.append(snippet_cleanliness)
    # 截断率惩罚 — 截断条目越多，可用性越低
    truncation_penalty = 0.0
    if total_snippets > 0:
        truncation_penalty = min(1.0, sum(
            1 for reqs in (normalized_reqs or {}).values()
            for req in reqs
            if _is_truncated_field(req.raw_text) or _is_truncated_field(req.param_name)
        ) / max(total_snippets, 1))
    usability_factors.append(max(0.0, 1.0 - truncation_penalty))
    # 跨包污染惩罚
    usability_factors.append(max(0.0, 1.0 - contamination_rate * 3))
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
    enforce_material_warnings = workflow_stage == "evidence_ready"
    enforce_content_warnings = workflow_stage in ("product_only", "evidence_ready")
    if focus_score < t["single_package_focus_score"] and len(target_package_ids) == 1:
        warnings.append(f"单包聚焦度不足: {focus_score:.2f} < {t['single_package_focus_score']}")
    if contamination_rate > t["package_contamination_rate"]:
        warnings.append(f"包件污染率过高: {contamination_rate:.2f} > {t['package_contamination_rate']}")
    if mixing_rate > t["table_category_mixing_rate"]:
        warnings.append(f"表格混装率过高: {mixing_rate:.2f} > {t['table_category_mixing_rate']}")
    if enforce_material_warnings and evidence_cov < t["bid_evidence_coverage"]:
        warnings.append(f"证据覆盖率不足: {evidence_cov:.2f} < {t['bid_evidence_coverage']}")
    if enforce_material_warnings and leakage > t["placeholder_leakage"]:
        warnings.append(f"占位符泄漏过多: {leakage:.2f} > {t['placeholder_leakage']}")
    if enforce_content_warnings and config_score < t["config_detail_score"]:
        warnings.append(f"配置详细度不足: {config_score:.2f} < {t['config_detail_score']}")
    if enforce_content_warnings and fact_density < t["fact_density_per_page"]:
        warnings.append(f"每页事实密度不足: {fact_density:.1f} < {t['fact_density_per_page']}")
    if snippet_cleanliness < t["snippet_cleanliness_score"]:
        warnings.append(f"原文片段清洁度不足: {snippet_cleanliness:.2f} < {t['snippet_cleanliness_score']}")
    if enforce_material_warnings and draft_usability < t["draft_usability_score"]:
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


def _check_required_headings(full_text: str) -> list[str]:
    required = [
        "一、响应文件封面格式",
        "四、技术偏离及详细配置明细表",
        "五、技术服务和售后服务的内容及措施",
        "资格性审查响应对照表",
        "符合性审查响应对照表",
        "详细评审响应对照表",
        "投标无效情形汇总及自检表",
    ]
    if "二、首轮报价表" in full_text:
        required.append("六、法定代表人/单位负责人授权书")
    if "二、报价书" in full_text:
        required.extend(["二、报价书", "三、报价一览表", "四、资格承诺函"])
    missing = [x for x in required if x not in full_text]
    for token in ("详见采购文件技术要求", "按采购文件售后服务要求执行"):
        if token in full_text:
            missing.append(f"存在泛化兜底文本：{token}")
    return missing


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
            if stripped.startswith("#") and _HEAL_TECH_TABLE_HDR.search(stripped):
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
    # 如果正文里已经有结构化的非技术响应章节，则只做“从技术表移除”，
    # 不再追加“自动分离”尾章。
    has_structured_nontech_section = any(
        "## 四、售后服务/配置/验收/资料要求响应" in (section.content or "")
        for section in healed
    )

    # 将提取出的非技术行组成独立分表章节
    if extracted_svc_lines and not has_structured_nontech_section:
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

    if extracted_doc_lines and not has_structured_nontech_section:
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
