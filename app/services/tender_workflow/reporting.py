from __future__ import annotations

import app.services.tender_workflow.common as _common
import app.services.tender_workflow.classification as _classification
import app.services.tender_workflow.product_facts as _product_facts
import app.services.tender_workflow.evidence as _evidence
import app.services.tender_workflow.materialization as _materialization
import app.services.tender_workflow.sanitization as _sanitization
import importlib

def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _classification, _product_facts, _evidence, _materialization, _sanitization,):
    __reexport_all(_module)

del _module

def _workflow_api():
    return importlib.import_module("app.services.tender_workflow")
def _build_regression_report(
    stages: list[dict[str, Any]],
    consistency_result: dict[str, Any] | None,
    review_result: dict[str, Any] | None,
    sanitize_result: dict[str, Any] | None,
    evidence_result: dict[str, Any] | None,
    *,
    normalized_result: dict[str, Any] | None = None,
    product_fact_result: dict[str, Any] | None = None,
    sections: list[BidDocumentSection] | None = None,
    selected_packages: list[str] | None = None,
) -> dict[str, Any]:
    stage_count = len(stages) + 1
    completed_count = len([stage for stage in stages if stage.get("status") == _STAGE_STATUS_COMPLETED])
    warning_count = len([stage for stage in stages if stage.get("status") == _STAGE_STATUS_WARNING])
    blocked_count = len([stage for stage in stages if stage.get("status") == _STAGE_STATUS_BLOCKED])

    evidence_rate = 0.0
    bidder_evidence_rate = 0.0
    proven_completion_rate = 0.0
    match_rate = 0.0
    if evidence_result:
        try:
            evidence_rate = float(evidence_result.get("binding_rate", 0.0))
        except (TypeError, ValueError):
            evidence_rate = 0.0
        try:
            bidder_evidence_rate = float(evidence_result.get("bidder_binding_rate", 0.0))
        except (TypeError, ValueError):
            bidder_evidence_rate = 0.0
        try:
            proven_completion_rate = float(evidence_result.get("proven_completion_rate", 0.0))
        except (TypeError, ValueError):
            proven_completion_rate = 0.0
        try:
            match_rate = float(evidence_result.get("match_rate", 0.0))
        except (TypeError, ValueError):
            match_rate = 0.0

    compliance_score = 0.0
    ready_for_submission = False
    if review_result:
        try:
            compliance_score = float(review_result.get("compliance_score", 0.0))
        except (TypeError, ValueError):
            compliance_score = 0.0
        ready_for_submission = bool(review_result.get("ready_for_submission", False))

    consistency_ok = bool(consistency_result) and consistency_result.get("overall_status") == "通过"
    outbound_ok = bool(sanitize_result) and sanitize_result.get("status") == "通过"
    regression_checks = [
        {
            "name": "十一层链路完整性",
            "status": "通过" if stage_count >= 10 and blocked_count == 0 else "需修订",
            "detail": f"阶段总数 {stage_count}，阻断阶段 {blocked_count} 个。",
        },
        {
            "name": "条款定位覆盖率",
            "status": "通过" if evidence_rate >= 0.5 else "需修订",
            "detail": f"当前覆盖率 {evidence_rate:.0%}。",
        },
        {
            "name": "投标方证据覆盖率",
            "status": "通过" if bidder_evidence_rate >= 0.5 else "需修订",
            "detail": f"当前覆盖率 {bidder_evidence_rate:.0%}。",
        },
        {
            "name": "要求-产品匹配率",
            "status": "通过" if match_rate >= 0.6 else "需修订",
            "detail": f"当前匹配率 {match_rate:.0%}。",
        },
        {
            "name": "已证实响应完成率",
            "status": "通过" if proven_completion_rate >= _MIN_PROVEN_COMPLETION_RATE else "需修订",
            "detail": f"当前完成率 {proven_completion_rate:.0%}。",
        },
        {
            "name": "硬校验结果",
            "status": "通过" if consistency_ok else "需修订",
            "detail": consistency_result.get("summary", "未执行") if consistency_result else "未执行",
        },
        {
            "name": "合规得分门槛",
            "status": "通过" if compliance_score >= 80 else "需修订",
            "detail": f"当前合规得分 {compliance_score:.1f}。",
        },
        {
            "name": "外发安全性",
            "status": "通过" if outbound_ok else "需修订",
            "detail": sanitize_result.get("summary", "未执行") if sanitize_result else "未执行",
        },
    ]

    # --- 6 new practical eval metrics ---

    # 1. package_isolation_score: ratio of section text that only mentions target packages
    _selected = set(selected_packages or [])
    unexpected_mentions: set[str] = set()
    if _selected and sections:
        full_text = "\n".join(sec.content for sec in sections)
        all_pkg_mentions = set(m.group(1) or m.group(2) for m in re.finditer(r"第\s*(\d+)\s*包|包\s*(\d+)", full_text))
        unexpected_mentions = all_pkg_mentions - _selected
        package_isolation = 1.0 if not unexpected_mentions else max(0.0, 1.0 - len(unexpected_mentions) / max(1, len(all_pkg_mentions)))
    else:
        package_isolation = 1.0
    _iso_detail = f"包件隔离度 {package_isolation:.0%}"
    if unexpected_mentions:
        _iso_detail += f"（存在串包：{','.join(sorted(unexpected_mentions))}）"
    _iso_detail += "。"
    regression_checks.append({
        "name": "single_package_focus_score",
        "status": "通过" if package_isolation >= 0.9 else "需修订",
        "detail": _iso_detail,
        "value": round(package_isolation, 4),
    })

    # 2. atomic_requirement_rate: fraction of technical requirements that are NOT generic/collapsed
    tech_reqs = (normalized_result or {}).get("technical_requirements", [])
    atomic_count = 0
    if tech_reqs:
        atomic_count = sum(1 for r in tech_reqs if isinstance(r, dict) and not _is_generic_value(_safe_text(r.get("normalized_value"))))
        atomic_rate = atomic_count / len(tech_reqs)
    else:
        atomic_rate = 0.0
    regression_checks.append({
        "name": "atomic_requirement_rate",
        "status": "通过" if atomic_rate >= 0.7 else "需修订",
        "detail": f"原子级需求占比 {atomic_rate:.0%}（{atomic_count if tech_reqs else 0}/{len(tech_reqs)} 项为具体参数）。",
        "value": round(atomic_rate, 4),
    })

    # 3. offered_fact_coverage: offered_fact_count / max(1, total technical requirements)
    offered_count = (product_fact_result or {}).get("offered_fact_count", 0)
    offered_fact_coverage = offered_count / max(1, len(tech_reqs)) if tech_reqs else (1.0 if offered_count else 0.0)
    regression_checks.append({
        "name": "offered_fact_coverage",
        "status": "通过" if offered_fact_coverage >= 0.5 else "需修订",
        "detail": f"产品事实覆盖率 {min(offered_fact_coverage, 1.0):.0%}（{offered_count} 条事实 / {len(tech_reqs)} 项技术要求）。",
        "value": round(min(offered_fact_coverage, 1.0), 4),
    })

    # 4. bid_evidence_coverage: bidder_matched_count / max(1, total bindings)
    bidder_matched = int((evidence_result or {}).get("bidder_matched_count", 0))
    total_bindings = int((evidence_result or {}).get("total", 0))
    bid_evidence_coverage = bidder_matched / max(1, total_bindings) if total_bindings else 0.0
    regression_checks.append({
        "name": "bid_evidence_coverage",
        "status": "通过" if bid_evidence_coverage >= 0.5 else "需修订",
        "detail": f"投标方证据覆盖率 {bid_evidence_coverage:.0%}（{bidder_matched}/{total_bindings} 项已绑定）。",
        "value": round(bid_evidence_coverage, 4),
    })

    # 5. config_pollution_rate: fraction of config-table rows that look like non-config items
    _CONFIG_POLLUTION_KEYWORDS = ("评分标准", "评分办法", "商务条款", "合同条款", "投标人须知", "售后服务", "违约责任", "评审")
    config_total = 0
    config_polluted = 0
    for sec in (sections or []):
        for line in sec.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("|") and "配置名称" not in stripped and not stripped.startswith("|---"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if len(cells) >= 4 and any(kw in cells[1] for kw in _CONFIG_POLLUTION_KEYWORDS):
                    config_polluted += 1
                if len(cells) >= 4 and cells[1].strip() and not re.fullmatch(r"\d+", cells[0].strip()):
                    continue
                if len(cells) >= 4:
                    config_total += 1
    config_pollution_rate = config_polluted / max(1, config_total)
    regression_checks.append({
        "name": "package_contamination_rate",
        "status": "通过" if config_pollution_rate <= 0.05 else "需修订",
        "detail": f"配置表污染率 {config_pollution_rate:.0%}（{config_polluted}/{config_total} 行疑似非配置项）。",
        "value": round(config_pollution_rate, 4),
    })

    config_rows_by_pkg: dict[str, int] = {}
    deviation_rows_by_pkg: dict[str, int] = {}
    evidence_rows_by_pkg: dict[str, int] = {}
    current_mode = ""
    current_pkg = ""
    for sec in (sections or []):
        for line in sec.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                if "详细配置明细表" in stripped or "配置清单" in stripped:
                    current_mode = "config"
                elif "技术偏离" in stripped:
                    current_mode = "deviation"
                elif "技术条款证据映射表" in stripped:
                    current_mode = "mapping"
                else:
                    current_mode = ""
                pkg_match = re.search(r"第\s*(\d+)\s*包", stripped)
                current_pkg = pkg_match.group(1) if pkg_match else ""
                if current_mode == "config" and current_pkg:
                    config_rows_by_pkg.setdefault(current_pkg, 0)
                if current_mode == "deviation" and current_pkg:
                    deviation_rows_by_pkg.setdefault(current_pkg, 0)
                if current_mode == "mapping" and current_pkg:
                    evidence_rows_by_pkg.setdefault(current_pkg, 0)
                continue

            if not stripped.startswith("|") or stripped.startswith("|---"):
                continue
            if current_mode == "config" and not stripped.startswith("| 序号"):
                cells = [c.strip() for c in stripped.split("|")]
                if (
                    current_pkg
                    and len(cells) >= 7
                    and re.match(r"^\d+$", cells[1].strip())
                    and cells[2].strip()
                ):
                    config_rows_by_pkg[current_pkg] = config_rows_by_pkg.get(current_pkg, 0) + 1
            elif current_mode == "deviation" and not stripped.startswith("| 条款编号") and not stripped.startswith("| 序号"):
                if current_pkg:
                    deviation_rows_by_pkg[current_pkg] = deviation_rows_by_pkg.get(current_pkg, 0) + 1
            elif current_mode == "mapping" and not stripped.startswith("| 序号"):
                cells = [c.strip() for c in stripped.split("|")]
                if current_pkg and len(cells) >= 5 and re.match(r"^\d+$", cells[1].strip()):
                    evidence_rows_by_pkg[current_pkg] = evidence_rows_by_pkg.get(current_pkg, 0) + 1

    min_config_items = _DETAIL_TARGETS["config_items_min"]
    if config_rows_by_pkg:
        config_detail_score = sum(
            min(count / max(1, min_config_items), 1.0)
            for count in config_rows_by_pkg.values()
        ) / len(config_rows_by_pkg)
    else:
        config_detail_score = 0.0
    regression_checks.append({
        "name": "config_detail_score",
        "status": "通过" if config_detail_score >= 0.8 else "需修订",
        "detail": (
            f"配置详细度 {config_detail_score:.0%}（"
            + "，".join(f"包{pkg}:{count}项" for pkg, count in sorted(config_rows_by_pkg.items()))
            + "）。"
            if config_rows_by_pkg
            else "未检测到可评估的配置明细表。"
        ),
        "value": round(config_detail_score, 4),
    })

    # fact_density_per_page: count of concrete facts / estimated page count
    total_content_chars = sum(len(sec.content) for sec in (sections or []))
    estimated_pages = max(1, total_content_chars // 1500)  # ~1500 chars per page
    fact_count = sum(1 for sec in (sections or []) for line in sec.content.splitlines()
                     if line.strip().startswith("|") and not line.strip().startswith("|---")
                     and not any(h in line for h in ("序号", "条款编号", "参数名称")))
    fact_density = fact_count / estimated_pages
    regression_checks.append({
        "name": "fact_density_per_page",
        "status": "通过" if fact_density >= 3.0 else "需修订",
        "detail": f"每页事实密度 {fact_density:.1f}（{fact_count} 条事实 / ~{estimated_pages} 页）。",
        "value": round(fact_density, 2),
    })

    # table_category_mixing_rate: fraction of deviation table rows that contain service/acceptance keywords
    mixing_count = 0
    deviation_total = sum(deviation_rows_by_pkg.values()) if deviation_rows_by_pkg else 0
    for sec in (sections or []):
        in_deviation = False
        for line in sec.content.splitlines():
            stripped = line.strip()
            if "技术偏离" in stripped and stripped.startswith("#"):
                in_deviation = True
                continue
            if stripped.startswith("#") and in_deviation:
                in_deviation = False
                continue
            if in_deviation and stripped.startswith("|") and not stripped.startswith("|---"):
                if any(kw in stripped for kw in ("售后", "培训", "维修", "保修", "验收", "安装调试")):
                    mixing_count += 1
    table_mixing_rate = mixing_count / max(1, deviation_total)
    regression_checks.append({
        "name": "table_category_mixing_rate",
        "status": "通过" if table_mixing_rate <= 0.05 else "需修订",
        "detail": f"表格分类混装率 {table_mixing_rate:.0%}（{mixing_count}/{deviation_total} 行含非技术类条款）。",
        "value": round(table_mixing_rate, 4),
    })

    total_tech_requirements = len(tech_reqs)
    total_deviation_rows = sum(deviation_rows_by_pkg.values())
    total_mapping_rows = sum(evidence_rows_by_pkg.values())
    mapping_denominator = max(1, total_tech_requirements, total_deviation_rows, total_mapping_rows)
    count_gap = (
        abs(total_tech_requirements - total_deviation_rows)
        + abs(total_tech_requirements - total_mapping_rows)
        + abs(total_deviation_rows - total_mapping_rows)
    )
    mapping_count_consistency = max(0.0, 1.0 - count_gap / (3 * mapping_denominator))
    regression_checks.append({
        "name": "mapping_count_consistency",
        "status": "通过" if mapping_count_consistency >= 0.8 else "需修订",
        "detail": (
            f"技术条款 {total_tech_requirements} 项，偏离表 {total_deviation_rows} 行，证据映射表 {total_mapping_rows} 行。"
        ),
        "value": round(mapping_count_consistency, 4),
    })

    template_like_markers = (
        "[待填写]",
        "[品牌型号]",
        "[生产厂家]",
        "[品牌]",
        "待核实",
        "详见招标文件",
        "按招标文件配置要求",
        "配置清单包含主机及全套标准附件",
        "具备完整的技术功能",
    )
    content_lines = [
        line.strip()
        for sec in (sections or [])
        for line in sec.content.splitlines()
        if line.strip()
    ]
    template_line_count = sum(
        1 for line in content_lines if any(marker in line for marker in template_like_markers)
    )
    section_template_similarity = template_line_count / max(1, len(content_lines))
    regression_checks.append({
        "name": "placeholder_leakage",
        "status": "通过" if section_template_similarity <= 0.2 else "需修订",
        "detail": f"模板化行占比 {section_template_similarity:.0%}（{template_line_count}/{len(content_lines)} 行）。",
        "value": round(section_template_similarity, 4),
    })

    # 6. external_block_rate: 1.0 if blocked, 0.0 if passed
    external_blocked = 1.0 if (sanitize_result and str(sanitize_result.get("status", "")).strip() == "阻断外发") else 0.0
    regression_checks.append({
        "name": "external_block_rate",
        "status": "通过" if external_blocked == 0.0 else "需修订",
        "detail": "外发未阻断" if external_blocked == 0.0 else f"外发已阻断：{'；'.join((sanitize_result or {}).get('blocked_reasons', ['未知原因'])[:3])}",
        "value": external_blocked,
    })
    # --- End new metrics ---

    # --- 7. 详细度目标（Detail Targets）检查 ---
    # 7a. 每包原子条款数检查
    tech_reqs_by_pkg: dict[str, int] = {}
    for r in tech_reqs:
        if isinstance(r, dict):
            pkg_id = _safe_text(r.get("package_id"))
            tech_reqs_by_pkg[pkg_id] = tech_reqs_by_pkg.get(pkg_id, 0) + 1
    min_atomic = _DETAIL_TARGETS["technical_atomic_clauses_per_package"]
    atomic_target_pass = all(
        count >= min_atomic for count in tech_reqs_by_pkg.values()
    ) if tech_reqs_by_pkg else False
    atomic_detail_parts = [f"包{pk}:{cnt}条" for pk, cnt in sorted(tech_reqs_by_pkg.items())]
    regression_checks.append({
        "name": "detail_target_atomic_clauses",
        "status": "通过" if atomic_target_pass else "需修订",
        "detail": (
            f"各包原子条款数：{', '.join(atomic_detail_parts) or '无'}。"
            f"目标：每包≥{min_atomic}条。"
        ),
        "value": min(tech_reqs_by_pkg.values()) if tech_reqs_by_pkg else 0,
    })

    # 7b. 偏离表最少行数
    deviation_rows_by_pkg: dict[str, int] = {}
    for sec in (sections or []):
        in_deviation = False
        current_pkg = ""
        for line in sec.content.splitlines():
            stripped = line.strip()
            if "技术偏离" in stripped and stripped.startswith("#"):
                in_deviation = True
                pkg_match = re.search(r"第\s*(\d+)\s*包", stripped)
                current_pkg = pkg_match.group(1) if pkg_match else "?"
                deviation_rows_by_pkg.setdefault(current_pkg, 0)
            elif stripped.startswith("#") and in_deviation:
                in_deviation = False
            elif in_deviation and stripped.startswith("|") and not stripped.startswith("|---") and not stripped.startswith("| 条款编号") and not stripped.startswith("| 序号"):
                deviation_rows_by_pkg[current_pkg] = deviation_rows_by_pkg.get(current_pkg, 0) + 1
    min_dev_rows = _DETAIL_TARGETS["deviation_table_min_rows"]
    dev_target_pass = all(
        count >= min_dev_rows for count in deviation_rows_by_pkg.values()
    ) if deviation_rows_by_pkg else False
    dev_detail_parts = [f"包{pk}:{cnt}行" for pk, cnt in sorted(deviation_rows_by_pkg.items())]
    regression_checks.append({
        "name": "detail_target_deviation_rows",
        "status": "通过" if dev_target_pass else "需修订",
        "detail": (
            f"偏离表行数：{', '.join(dev_detail_parts) or '无'}。"
            f"目标：每包≥{min_dev_rows}行。"
        ),
        "value": min(deviation_rows_by_pkg.values()) if deviation_rows_by_pkg else 0,
    })

    # 7c. 叙述章节字数检查
    narrative_keywords = ("关键性能说明", "配置说明", "交付说明", "验收说明", "使用与培训说明")
    narrative_total_chars = 0
    for sec in (sections or []):
        for nk in narrative_keywords:
            if nk in sec.content:
                # Count chars in this section
                start_pos = sec.content.find(nk)
                narrative_total_chars += len(sec.content[start_pos:start_pos + 500])
    min_narrative = _DETAIL_TARGETS["narrative_sections_min_chars"]
    narrative_pass = narrative_total_chars >= min_narrative
    regression_checks.append({
        "name": "detail_target_narrative_chars",
        "status": "通过" if narrative_pass else "需修订",
        "detail": f"叙述章节总字数约 {narrative_total_chars} 字。目标：≥{min_narrative}字。",
        "value": narrative_total_chars,
    })

    # 7d. 证据覆盖率（每条至少1个证据）
    evidence_per_item_target = _DETAIL_TARGETS["evidence_per_item"]
    evidence_coverage = float((evidence_result or {}).get("evidence_coverage_rate", bidder_evidence_rate))
    evidence_target_pass = evidence_coverage >= 0.5
    regression_checks.append({
        "name": "detail_target_evidence_coverage",
        "status": "通过" if evidence_target_pass else "需修订",
        "detail": f"证据覆盖率 {evidence_coverage:.0%}。目标：每条至少{evidence_per_item_target}个证据。",
        "value": round(evidence_coverage, 4),
    })
    # --- End detail target checks ---

    # --- 8. 新增实用性评测指标 ---

    # 8a. 实际参数覆盖率 (actual_param_coverage): 偏离表中有真实参数值的行 / 总行数
    actual_param_rows = 0
    total_deviation_data_rows = sum(deviation_rows_by_pkg.values())
    _PENDING_MARKERS = ("待核实", "[待填写]", "[待补充]", "[品牌型号]", "[生产厂家]")
    for sec in (sections or []):
        in_deviation_section = False
        for line in sec.content.splitlines():
            stripped = line.strip()
            if "技术偏离" in stripped and stripped.startswith("#"):
                in_deviation_section = True
                continue
            if stripped.startswith("#") and in_deviation_section:
                in_deviation_section = False
                continue
            if not in_deviation_section or not stripped.startswith("|") or stripped.startswith("|---"):
                continue
            if any(h in stripped for h in ("条款编号", "序号", "参数名称")):
                continue
            # 检查响应列是否有真实值
            cells = [c.strip() for c in stripped.split("|")]
            if len(cells) >= 5:
                response_cell = cells[4] if len(cells) > 4 else ""
                has_real_value = bool(
                    response_cell
                    and response_cell not in ("", " ", "-")
                    and not any(pm in response_cell for pm in _PENDING_MARKERS)
                )
                if has_real_value:
                    actual_param_rows += 1

    actual_param_coverage = actual_param_rows / max(1, total_deviation_data_rows)
    regression_checks.append({
        "name": "actual_param_coverage",
        "status": "通过" if actual_param_coverage >= 0.7 else "需修订",
        "detail": f"实际参数覆盖率 {actual_param_coverage:.0%}（{actual_param_rows}/{total_deviation_data_rows} 行含真实参数值）。",
        "value": round(actual_param_coverage, 4),
    })

    # 8b. 投标侧证据页码覆盖率 (bid_evidence_page_coverage)
    bid_evidence_items = (evidence_result or {}).get("bid_evidence", [])
    if not isinstance(bid_evidence_items, list):
        bid_evidence_items = []
    items_with_page = sum(
        1 for item in bid_evidence_items
        if isinstance(item, dict) and item.get("evidence_page") is not None
    )
    total_bid_items = len(bid_evidence_items)
    bid_page_coverage = items_with_page / max(1, total_bid_items)
    regression_checks.append({
        "name": "bid_evidence_page_coverage",
        "status": "通过" if bid_page_coverage >= 0.5 else "需修订",
        "detail": f"投标侧证据页码覆盖率 {bid_page_coverage:.0%}（{items_with_page}/{total_bid_items} 项含页码引用）。",
        "value": round(bid_page_coverage, 4),
    })

    # 8c. 配置项平均条数 (config_avg_items_per_package)
    avg_config_items = (
        sum(config_rows_by_pkg.values()) / len(config_rows_by_pkg)
        if config_rows_by_pkg else 0.0
    )
    config_avg_pass = avg_config_items >= _DETAIL_TARGETS["config_items_min"]
    regression_checks.append({
        "name": "config_avg_items_per_package",
        "status": "通过" if config_avg_pass else "需修订",
        "detail": (
            f"配置项平均条数 {avg_config_items:.1f} 条/包"
            f"（{'，'.join(f'包{pk}:{cnt}项' for pk, cnt in sorted(config_rows_by_pkg.items())) or '无'}）。"
            f"目标：≥{_DETAIL_TARGETS['config_items_min']}条。"
        ),
        "value": round(avg_config_items, 2),
    })

    # 8d. 模板段落重复率 (template_paragraph_ratio) — 与 section_template_similarity 互补
    _TEMPLATE_PARAGRAPH_MARKERS = (
        "具备完整的技术功能",
        "能够满足采购文件要求",
        "配置清单包含主机及全套标准附件",
        "按招标文件配置要求",
        "由我公司负责运输至指定地点",
        "按照国家相关标准及招标文件要求",
        "采用专业包装方式",
        "安排专业培训师",
    )
    para_total = 0
    para_template = 0
    for sec in (sections or []):
        paragraphs = [p.strip() for p in sec.content.split("\n\n") if p.strip() and len(p.strip()) > 20]
        para_total += len(paragraphs)
        for para in paragraphs:
            if sum(1 for m in _TEMPLATE_PARAGRAPH_MARKERS if m in para) >= 2:
                para_template += 1
    template_paragraph_ratio = para_template / max(1, para_total)
    regression_checks.append({
        "name": "template_paragraph_ratio",
        "status": "通过" if template_paragraph_ratio <= 0.2 else "需修订",
        "detail": f"模板段落重复率 {template_paragraph_ratio:.0%}（{para_template}/{para_total} 段含多个模板标记）。",
        "value": round(template_paragraph_ratio, 4),
    })

    # 8e. external hard-gate 拦截率 (external_hardgate_block_items)
    hardgate_blocked_count = len((sanitize_result or {}).get("blocked_reasons", []))
    hardgate_total_checks = 10  # 总硬门检查项数
    hardgate_ratio = hardgate_blocked_count / hardgate_total_checks
    regression_checks.append({
        "name": "external_hardgate_block_rate",
        "status": "通过" if hardgate_blocked_count == 0 else "需修订",
        "detail": (
            "外发硬门全部通过" if hardgate_blocked_count == 0
            else f"外发硬门拦截 {hardgate_blocked_count} 项：{'；'.join((sanitize_result or {}).get('blocked_reasons', [])[:3])}"
        ),
        "value": round(hardgate_ratio, 4),
    })
    # --- End new practical eval metrics ---

    passed_count = len([item for item in regression_checks if item["status"] == "通过"])
    score = round(
        min(
            100.0,
            passed_count * 7
            + compliance_score * 0.15
            + evidence_rate * 4
            + bidder_evidence_rate * 4
            + match_rate * 5
            + proven_completion_rate * 5
            + package_isolation * 5
            + atomic_rate * 5
            + min(offered_fact_coverage, 1.0) * 5
            + bid_evidence_coverage * 5
            + (1.0 - config_pollution_rate) * 3
            + (1.0 - external_blocked) * 3,
        ),
        2,
    )
    overall_status = "通过" if ready_for_submission and blocked_count == 0 and outbound_ok else "需修订"
    summary = (
        f"回归评测完成：{passed_count}/{len(regression_checks)} 项通过，"
        f"阶段完成 {completed_count} 项，告警 {warning_count} 项。"
    )

    return {
        "overall_status": overall_status,
        "score": score,
        "ready_for_delivery": ready_for_submission and outbound_ok,
        "checks": regression_checks,
        "summary": summary,
    }


def _retrieve_citations(query: str, preferred_source: str | None = None, top_k: int = _DEFAULT_CITATION_TOP_K) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    try:
        hits = _workflow_api().search_knowledge(query=query, top_k=max(1, top_k))
    except Exception as exc:  # noqa: BLE001
        logger.warning("检索引用失败，query=%s, error=%s", query, exc)
        return []

    if not hits:
        return []

    if preferred_source:
        preferred_hits: list[dict[str, Any]] = []
        for hit in hits:
            metadata = hit.get("metadata", {})
            source = str(metadata.get("source", "")).strip()
            if source == preferred_source:
                preferred_hits.append(hit)
        if preferred_hits:
            hits = preferred_hits

    return _prepare_citations(hits, limit=top_k)


def _traceability_hits(
    technical_text: str,
    technical_matches: list[dict[str, Any]],
) -> tuple[int, int, list[str]]:
    if not technical_matches:
        return 0, 0, []

    hit_count = 0
    missing: list[str] = []
    for match in technical_matches:
        if not isinstance(match, dict):
            continue
        parameter_name = _safe_text(match.get("parameter_name"))
        if not parameter_name:
            continue
        evidence_bits = _dedupe_texts(
            [
                _safe_text(match.get("matched_fact_quote")),
                _safe_text(match.get("bidder_evidence_quote")),
                _safe_text(match.get("response_value")),
            ]
        )
        if parameter_name in technical_text and any(bit and bit in technical_text for bit in evidence_bits):
            hit_count += 1
        else:
            missing.append(parameter_name)

    return hit_count, len([item for item in technical_matches if isinstance(item, dict)]), missing


def _product_compliance_gaps(
    tender: TenderDocument,
    package_ids: list[str],
    products: dict[str, ProductSpecification],
) -> list[str]:
    if not package_ids:
        return []

    context = _workflow_context_text(tender)
    medical_project = _contains_any(context, _MEDICAL_KEYWORDS)
    imported_project = _contains_any(context, _IMPORTED_KEYWORDS)
    requires_energy_cert = _contains_any(context, ("节能", "环保", "能效"))
    gaps: list[str] = []

    for pkg_id in package_ids:
        product = products.get(pkg_id)
        if product is None:
            gaps.append(f"包{pkg_id} 未绑定产品资料")
            continue
        if medical_project and not product.registration_number.strip():
            gaps.append(f"包{pkg_id} 缺少注册证/备案编号")
        if imported_project and not product.origin.strip():
            gaps.append(f"包{pkg_id} 缺少原产地/合法来源")
        if imported_project and not product.authorization_letter.strip():
            gaps.append(f"包{pkg_id} 缺少授权链/报关材料")
        if requires_energy_cert and not product.certifications:
            gaps.append(f"包{pkg_id} 缺少节能环保认证")

    return gaps


def _material_coverage(required_materials: list[str], sections: list[BidDocumentSection]) -> tuple[int, int, list[str]]:
    if not required_materials:
        return 0, 0, []

    full_text = "\n".join(sec.content for sec in sections)
    full_text = full_text.lower()

    matched = 0
    missing: list[str] = []
    for item in required_materials:
        normalized = item.strip()
        if not normalized:
            continue
        tokens = [tok for tok in re.split(r"[，,、；;（）()\\s/]+", normalized) if len(tok) >= 2]
        if not tokens:
            tokens = [normalized]
        if any(token.lower() in full_text for token in tokens[:4]):
            matched += 1
        else:
            missing.append(normalized)

    total = len([x for x in required_materials if x.strip()])
    return matched, total, missing
