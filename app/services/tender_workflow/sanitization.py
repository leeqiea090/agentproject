from __future__ import annotations

import app.services.tender_workflow.common as _common
import app.services.tender_workflow.classification as _classification
import app.services.tender_workflow.product_facts as _product_facts
import app.services.tender_workflow.evidence as _evidence
import app.services.tender_workflow.materialization as _materialization

def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _classification, _product_facts, _evidence, _materialization,):
    __reexport_all(_module)

del _module
def _build_internal_audit_snapshot(
    ingestion_result: dict[str, Any],
    package_result: dict[str, Any],
    clause_result: dict[str, Any],
    normalized_result: dict[str, Any],
    product_fact_result: dict[str, Any],
    rule_result: dict[str, Any],
    evidence_result: dict[str, Any],
    validation_result: dict[str, Any],
    hard_validation_result: dict[str, Any] | None,
    sections: list[BidDocumentSection],
) -> dict[str, Any]:
    return {
        "ingestion_summary": ingestion_result.get("summary", ""),
        "selected_packages": package_result.get("selected_packages", []),
        "package_count": len(package_result.get("packages", [])),
        "clause_category_counts": {
            key: len(_ensure_str_list(value))
            for key, value in (clause_result.get("clause_categories", {}) or {}).items()
        },
        "normalized_counts": {
            "qualification": len(normalized_result.get("qualification_requirements", [])),
            "commercial": len(normalized_result.get("commercial_requirements", [])),
            "technical": len(normalized_result.get("technical_requirements", [])),
        },
        "product_fact_count": product_fact_result.get("fact_count", 0),
        "product_fact_packages": product_fact_result.get("packages", []),
        "branch_decisions": rule_result.get("branch_decisions", []),
        "manual_fill_items": rule_result.get("manual_fill_items", []),
        "blocking_fill_items": rule_result.get("blocking_fill_items", []),
        "material_missing_items": validation_result.get("missing_items", []),
        "evidence_binding_rate": evidence_result.get("binding_rate", 0.0),
        "bidder_binding_rate": evidence_result.get("bidder_binding_rate", 0.0),
        "match_rate": evidence_result.get("match_rate", 0.0),
        "proven_completion_rate": evidence_result.get("proven_completion_rate", 0.0),
        "technical_matches": evidence_result.get("technical_matches", []),
        "unproven_items": evidence_result.get("unproven_items", []),
        "hard_validation_issues": [] if not hard_validation_result else hard_validation_result.get("issues", []),
        "section_titles": [section.section_title for section in sections],
    }


def _normalize_pending_sections(
    sections: list[BidDocumentSection],
    *,
    add_draft_watermark: bool = False,
) -> list[BidDocumentSection]:
    normalized: list[BidDocumentSection] = []
    watermark = "**【待补充底稿 — 含未补齐信息，需人工补充后再外发】**\n\n"
    for section in sections:
        content = section.content
        content = content.replace("[投标方公司名称]", "待补充（投标人名称）")
        content = content.replace("[法定代表人]", "待补充（法定代表人）")
        content = content.replace("[授权代表]", "待补充（授权代表）")
        content = content.replace("[联系电话]", "待补充（联系电话）")
        content = content.replace("[联系地址]", "待补充（联系地址）")
        content = content.replace("[公司注册地址]", "待补充（公司注册地址）")
        content = content.replace("[品牌型号]", "待补充（品牌型号）")
        content = content.replace("[生产厂家]", "待补充（生产厂家）")
        content = content.replace("[品牌]", "待补充（品牌）")
        content = content.replace("[待填写]", "待补充")
        content = content.replace("[待补充]", "待补充")
        content = content.replace("待补投标方证据", "待补充（投标方证据）")
        content = content.replace("待补充投标方证据", "待补充（投标方证据）")
        content = content.replace("投标方证据待补充", "待补充（投标方证据）")
        content = content.replace("投标方证据：未绑定", "投标方证据：待补充")
        # 带括号的长模式必须在裸 catch-all 之前
        content = content.replace("待核实（未匹配到已证实产品事实）", "待补充（投标产品实参）")
        content = content.replace("待核实（需填入投标产品实参）", "待补充（投标产品实参）")
        # 裸 catch-all：只替换不带括号的裸"待核实"
        import re as _re
        content = _re.sub(r"待核实(?!（)", "待补充", content)
        # 展平嵌套
        from app.services.quality_gate import _flatten_nested_placeholders
        content = _flatten_nested_placeholders(content)
        if add_draft_watermark and not content.startswith("**【待补充底稿"):
            content = watermark + content
        normalized.append(section.model_copy(update={"content": content}))
    return normalized


def _sanitize_for_external_delivery(
    sections: list[BidDocumentSection],
    hard_validation_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
    document_mode: str | None = None,
) -> tuple[list[BidDocumentSection], dict[str, Any]]:
    cleaned_sections = _apply_template_pollution_guard(sections)
    changed_sections: list[str] = []
    placeholder_sections: list[str] = []
    unresolved_marker_sections: list[str] = []

    for original, cleaned in zip(sections, cleaned_sections, strict=False):
        if original.content != cleaned.content:
            changed_sections.append(cleaned.section_title)
        if _section_has_unresolved_delivery_content(cleaned.content):
            placeholder_sections.append(cleaned.section_title)
        # Separately track sections with unresolved *delivery* markers (half-finished evidence)
        if any(marker in cleaned.content for marker in _UNRESOLVED_DELIVERY_MARKERS):
            unresolved_marker_sections.append(cleaned.section_title)

    # --- Fix: check for critical placeholders that must block external delivery ---
    _CRITICAL_PLACEHOLDER_PATTERNS = (
        "[品牌型号]", "[生产厂家]", "[品牌]", "[待填写]", "[待补充]",
    )
    critical_placeholder_sections: list[str] = []
    for cleaned in cleaned_sections:
        if any(pattern in cleaned.content for pattern in _CRITICAL_PLACEHOLDER_PATTERNS):
            if cleaned.section_title not in critical_placeholder_sections:
                critical_placeholder_sections.append(cleaned.section_title)
    # --- End fix ---

    blocked_reasons: list[str] = []

    # 多包母版模式不允许外发
    if document_mode == "multi_package_draft":
        blocked_reasons.append("当前为多包母版模式（multi_package_draft），不允许外发")

    # --- 跨包污染检测 ---
    if normalized_result:
        selected_pkgs = set(r.get("package_id", "") for r in normalized_result.get("technical_requirements", []) if isinstance(r, dict))
        for cleaned in cleaned_sections:
            all_mentions = set()
            for m in re.finditer(r"第\s*(\d+)\s*包|包\s*(\d+)", cleaned.content):
                pkg_id = m.group(1) or m.group(2)
                if pkg_id:
                    all_mentions.add(pkg_id)
            foreign_pkgs = all_mentions - selected_pkgs
            if foreign_pkgs and selected_pkgs:
                blocked_reasons.append(
                    f"章节'{cleaned.section_title}'引用了非目标包（{','.join(sorted(foreign_pkgs))}），疑似跨包污染"
                )
    # --- End cross-package contamination check ---

    # --- 分类混表检测 ---
    for cleaned in cleaned_sections:
        if "技术偏离" in cleaned.section_title or "技术偏离" in cleaned.content[:100]:
            for line in cleaned.content.splitlines():
                if line.strip().startswith("|") and any(kw in line for kw in _SERVICE_KEYWORDS + _ACCEPTANCE_KEYWORDS):
                    if "技术偏离表混入服务/验收类条款，存在分类混表" not in blocked_reasons:
                        blocked_reasons.append("技术偏离表混入服务/验收类条款，存在分类混表")
                        break
    # --- End category mixing check ---

    # --- Content-quality hard gates ---
    # (a) Deviation table quality: block if only 1 generic row with "详见招标文件"
    _GENERIC_DEVIATION_MARKERS = ("详见招标文件采购需求", "详见招标文件", "详见拟投产品参数资料")
    deviation_table_generic = False
    deviation_rows_by_pkg: dict[str, int] = {}

    for cleaned in cleaned_sections:
        if "技术偏离" not in cleaned.section_title and "技术偏离" not in cleaned.content:
            continue
        deviation_lines = [
            line for line in cleaned.content.splitlines()
            if line.strip().startswith("|") and not line.strip().startswith("|---") and not line.strip().startswith("| 序号")
        ]
        # If there's only 0-1 data row and it's generic
        if len(deviation_lines) <= 1 and any(
            any(m in line for m in _GENERIC_DEVIATION_MARKERS) for line in deviation_lines
        ):
            deviation_table_generic = True

        # 统计每包的偏离表行数
        current_pkg = ""
        for line in cleaned.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading_pkg = _extract_heading_package_id(stripped)
                if heading_pkg:
                    current_pkg = heading_pkg
                if "技术偏离" in stripped:
                    current_pkg = current_pkg or "?"
                    deviation_rows_by_pkg.setdefault(current_pkg or "?", 0)
                continue
            elif stripped.startswith("|") and not stripped.startswith("|---") and not stripped.startswith("| 序号") and not stripped.startswith("| 条款"):
                if not current_pkg or current_pkg == "?":
                    clause_cells = [cell.strip() for cell in stripped.strip("|").split("|")]
                    if clause_cells:
                        clause_match = re.match(r"^(\d+)\.", clause_cells[0])
                        if clause_match:
                            current_pkg = clause_match.group(1)
                            deviation_rows_by_pkg.setdefault(current_pkg, 0)
                if current_pkg:
                    deviation_rows_by_pkg[current_pkg] = deviation_rows_by_pkg.get(current_pkg, 0) + 1

    if deviation_table_generic:
        blocked_reasons.append("技术偏离表仅有1行笼统条目（详见招标文件），未逐条展开参数")

    # (新增) 检查偏离表行数是否达到最低门槛
    min_dev_rows = _DETAIL_TARGETS["deviation_table_min_rows"]
    for pkg_id, row_count in deviation_rows_by_pkg.items():
        if row_count < min_dev_rows:
            blocked_reasons.append(f"包{pkg_id}技术偏离表仅{row_count}行，少于最低要求{min_dev_rows}行")

    # (b) Evidence mapping quality: block if bidder evidence coverage is 0
    if evidence_result:
        bidder_count = int(evidence_result.get("bidder_matched_count", 0))
        total_bindings = int(evidence_result.get("total", 0))
        proven_rate = float(evidence_result.get("proven_completion_rate", 1.0))
        evidence_coverage = float(evidence_result.get("evidence_coverage_rate", 0.0))

        if total_bindings > 0 and bidder_count == 0:
            blocked_reasons.append("证据映射无任何投标方证据绑定，需补充产品参数或证照")
        elif total_bindings > 0 and proven_rate < 0.3:
            blocked_reasons.append(f"已证实响应完成率仅 {proven_rate:.0%}，远低于外发门槛")

        # (新增) 检查证据覆盖率门槛
        if evidence_coverage < 0.5:
            blocked_reasons.append(f"证据覆盖率仅{evidence_coverage:.0%}，少于最低要求50%")

    # (新增c) 检查技术条款数量门槛
    if normalized_result:
        tech_reqs = normalized_result.get("technical_requirements", [])
        tech_reqs_by_pkg: dict[str, int] = {}
        for r in tech_reqs:
            if isinstance(r, dict):
                pkg_id = _safe_text(r.get("package_id"))
                tech_reqs_by_pkg[pkg_id] = tech_reqs_by_pkg.get(pkg_id, 0) + 1

        min_tech_clauses = _DETAIL_TARGETS["technical_atomic_clauses_per_package"]
        for pkg_id, clause_count in tech_reqs_by_pkg.items():
            if clause_count < min_tech_clauses:
                blocked_reasons.append(f"包{pkg_id}技术条款仅{clause_count}条，少于最低要求{min_tech_clauses}条")

    # (新增d) 检查配置项数量门槛
    min_config_items = _DETAIL_TARGETS["config_items_min"]
    config_rows_by_pkg: dict[str, int] = {}
    for cleaned in cleaned_sections:
        if not any(marker in cleaned.content for marker in ("详细配置明细表", "配置清单", "配置说明")):
            continue
        current_pkg = ""
        for line in cleaned.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading_pkg = _extract_heading_package_id(stripped)
                if heading_pkg:
                    current_pkg = heading_pkg
                if any(marker in stripped for marker in ("详细配置明细表", "配置清单")):
                    current_pkg = current_pkg or "?"
                    config_rows_by_pkg.setdefault(current_pkg or "?", 0)
                continue
            elif stripped.startswith("|") and not stripped.startswith("|---") and not stripped.startswith("| 序号"):
                if current_pkg and "|" in stripped:
                    cells = [c.strip() for c in stripped.split("|")]
                    # 有效配置行：明细表至少包含配置名称/数量/用途说明等列，且首列为序号
                    if (
                        len(cells) >= 7
                        and re.match(r"^\d+$", cells[1].strip())
                        and cells[2].strip()
                        and cells[5].strip()
                    ):
                        config_rows_by_pkg[current_pkg] = config_rows_by_pkg.get(current_pkg, 0) + 1

    for pkg_id, config_count in config_rows_by_pkg.items():
        if config_count < min_config_items:
            blocked_reasons.append(f"包{pkg_id}配置项仅{config_count}项，少于最低要求{min_config_items}项")

    # (新增e) 检查详细说明章节是否存在
    has_detailed_explanation = any(
        "详细说明" in sec.content or "详细技术说明" in sec.content or "关键性能说明" in sec.content
        for sec in cleaned_sections
    )
    if not has_detailed_explanation:
        blocked_reasons.append("缺少详细技术说明章节，技术部分过于简单")

    # (新增f) 检查每条响应是否只有一句话（过于简单）
    single_sentence_count = 0
    for cleaned in cleaned_sections:
        if "技术偏离" not in cleaned.content:
            continue
        for line in cleaned.content.splitlines():
            if line.strip().startswith("|") and "投标产品响应" in line:
                # 提取响应列内容
                cells = [c.strip() for c in line.split("|")]
                if len(cells) >= 5:
                    response_text = cells[4]  # 通常是第4列
                    # 检查是否只有一句话且少于20字
                    if response_text and len(response_text) < 20 and response_text.count("。") <= 1:
                        single_sentence_count += 1

    if single_sentence_count >= 5:
        blocked_reasons.append(f"发现{single_sentence_count}条响应过于简单（不足20字），需补充详细说明")
    # --- End content-quality hard gates ---

    # --- 增强 Hard Gate: internal 与 external 真正分流 ---
    # 硬性阻断条件（出现任何一项即禁止 external draft）:
    _HARD_BLOCK_MARKERS = (
        "[待填写]",
        "待核实",
        "待补投标方证据",
        "待补充投标方证据",
        "需补充产品参数或证照",
        "待补证",
        "待定位片段",
        "底稿阶段",
    )
    hard_block_sections: dict[str, list[str]] = {}
    for cleaned in cleaned_sections:
        found_markers = []
        for marker in _HARD_BLOCK_MARKERS:
            if marker in cleaned.content:
                found_markers.append(marker)
        if found_markers:
            hard_block_sections[cleaned.section_title] = found_markers

    if hard_block_sections:
        sample_sections = list(hard_block_sections.keys())[:3]
        sample_markers = set()
        for markers in hard_block_sections.values():
            sample_markers.update(markers)
        blocked_reasons.append(
            f"发现 {len(hard_block_sections)} 个章节含 internal draft 标记"
            f"（{', '.join(sorted(sample_markers)[:4])}）"
            f"，涉及：{';'.join(sample_sections)}"
        )

    # 检查关键参数未填（技术偏离表中关键列为空）
    empty_key_param_count = 0
    for cleaned in cleaned_sections:
        if "技术偏离" not in cleaned.content:
            continue
        for line in cleaned.content.splitlines():
            if not line.strip().startswith("|") or line.strip().startswith("|---"):
                continue
            cells = [c.strip() for c in line.split("|")]
            if len(cells) >= 6:
                response_cell = cells[4] if len(cells) > 4 else ""
                if not response_cell or response_cell in ("", " ", "-"):
                    empty_key_param_count += 1
    if empty_key_param_count >= 3:
        blocked_reasons.append(f"技术偏离表中有 {empty_key_param_count} 项关键参数响应为空")

    # 检查证据列页码空白
    evidence_page_blank_count = 0
    for cleaned in cleaned_sections:
        if "证据" not in cleaned.content and "映射" not in cleaned.content:
            continue
        for line in cleaned.content.splitlines():
            if not line.strip().startswith("|") or line.strip().startswith("|---"):
                continue
            cells = [c.strip() for c in line.split("|")]
            # 检查证据/页码列是否空白
            for idx, cell in enumerate(cells):
                if idx > 0 and ("页码" in str(cells[0] if idx > 0 else "") or "证据" in str(cells[0] if idx > 0 else "")):
                    if not cell or cell in ("", " ", "-", "待补充"):
                        evidence_page_blank_count += 1
    if evidence_page_blank_count >= 5:
        blocked_reasons.append(f"证据映射表中有 {evidence_page_blank_count} 项页码空白")
    # --- End enhanced hard gates ---

    if hard_validation_result and hard_validation_result.get("overall_status") != "通过":
        blocked_reasons.append("硬校验未通过")
    if evidence_result and float(evidence_result.get("proven_completion_rate", 1.0)) < _MIN_PROVEN_COMPLETION_RATE:
        blocked_reasons.append("已证实完成率未达外发门槛")
    # Block external delivery when half-finished evidence markers are still present
    if unresolved_marker_sections:
        blocked_reasons.append(
            f"存在 {len(unresolved_marker_sections)} 个章节包含未解决的投标响应标记"
            f"（{';'.join(unresolved_marker_sections[:3])}）"
        )
    # Block external delivery when critical placeholders (brand/model/manufacturer) are present
    if critical_placeholder_sections:
        blocked_reasons.append(
            f"存在 {len(critical_placeholder_sections)} 个章节包含关键占位符"
            f"（品牌型号/生产厂家等未填写：{';'.join(critical_placeholder_sections[:3])}）"
        )

    # 计算 draft_level: internal / external
    draft_level = "external" if not blocked_reasons else "internal"

    if blocked_reasons:
        status = "阻断外发"
    else:
        status = "通过" if not placeholder_sections else "需人工终审"
    summary = (
        f"已完成外发净化，共清理 {len(changed_sections)} 个章节。"
        if changed_sections
        else "章节内容未发现明显模板污染。"
    )
    if placeholder_sections:
        summary += f" 仍有 {len(placeholder_sections)} 个章节包含占位符。"
    if unresolved_marker_sections:
        summary += f" {len(unresolved_marker_sections)} 个章节含未解决的投标响应标记，已阻断外发。"
    if critical_placeholder_sections:
        summary += f" {len(critical_placeholder_sections)} 个章节含关键占位符（品牌型号/生产厂家等），已阻断外发。"
    if blocked_reasons:
        summary += f" 当前外发已阻断：{'；'.join(blocked_reasons[:5])}。"
    summary += f" 当前稿件级别：{draft_level}。"

    output_sections = (
        _normalize_pending_sections(cleaned_sections, add_draft_watermark=True)
        if blocked_reasons
        else cleaned_sections
    )

    return output_sections, {
        "status": status,
        "draft_level": draft_level,
        "changed_sections": changed_sections,
        "placeholder_sections": placeholder_sections,
        "unresolved_marker_sections": unresolved_marker_sections,
        "hard_block_sections": hard_block_sections,
        "blocked_reasons": blocked_reasons,
        "summary": summary,
    }
