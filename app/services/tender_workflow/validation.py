from __future__ import annotations

import app.services.tender_workflow.common as _common
import app.services.tender_workflow.classification as _classification
import app.services.tender_workflow.product_facts as _product_facts
import app.services.tender_workflow.evidence as _evidence
import app.services.tender_workflow.materialization as _materialization
import app.services.tender_workflow.sanitization as _sanitization
import app.services.tender_workflow.reporting as _reporting

def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _classification, _product_facts, _evidence, _materialization, _sanitization, _reporting,):
    __reexport_all(_module)

del _module
def _second_validation(
    analysis_result: dict[str, Any],
    validation_result: dict[str, Any],
    sections: list[BidDocumentSection],
    generation_result: dict[str, Any] | None = None,
    tender: TenderDocument | None = None,
    selected_packages: list[str] | None = None,
    products: dict[str, ProductSpecification] | None = None,
    evidence_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    check_items: list[dict[str, str]] = []
    issues: list[str] = []
    suggestions: list[str] = []

    validation_status = str(validation_result.get("overall_status", "")).strip()
    material_pass = validation_status == "通过"
    check_items.append(
        {
            "name": "资料完整性复核",
            "status": "通过" if material_pass else "需修订",
            "detail": f"第二步校验状态：{validation_status or '未提供'}",
        }
    )
    if not material_pass:
        issues.append("资料校验未通过，存在缺失项或待确认项。")
        suggestions.append("先完成缺失资料补齐，再重新运行流程。")

    section_titles = [sec.section_title for sec in sections]
    required_chapters = ("第一章", "第二章", "第三章", "第四章")
    missing_chapters = [
        chapter
        for chapter in required_chapters
        if not any(chapter in title for title in section_titles)
    ]
    chapter_pass = not missing_chapters
    chapter_detail = "章节完整" if chapter_pass else f"缺少章节：{', '.join(missing_chapters)}"
    check_items.append(
        {
            "name": "分章节完整性",
            "status": "通过" if chapter_pass else "需修订",
            "detail": chapter_detail,
        }
    )
    if not chapter_pass:
        issues.append(f"分章节生成不完整：{', '.join(missing_chapters)}。")
        suggestions.append("补齐缺失章节，确保投标文件结构完整。")

    placeholder_total = 0
    placeholder_section_details: list[str] = []
    for sec in sections:
        count = 0
        for pattern in _PLACEHOLDER_PATTERNS:
            count += sec.content.count(pattern)
        if count > 0:
            placeholder_total += count
            placeholder_section_details.append(f"{sec.section_title}({count}处)")

    placeholder_pass = placeholder_total == 0
    placeholder_detail = (
        "未发现占位符。"
        if placeholder_pass
        else f"发现 {placeholder_total} 处占位符：{'；'.join(placeholder_section_details)}"
    )
    check_items.append(
        {
            "name": "占位符与留空项检查",
            "status": "通过" if placeholder_pass else "需修订",
            "detail": placeholder_detail,
        }
    )
    if not placeholder_pass:
        issues.append("标书中仍存在未替换占位符或留空说明。")
        suggestions.append("逐章替换 [待填写]/公司信息占位符，并补齐附件留空项。")

    technical_text = "\n".join(
        sec.content
        for sec in sections
        if "第三章" in sec.section_title or "技术" in sec.section_title
    )
    proven_completion = evidence_result or {}
    technical_matches = [
        item
        for item in (proven_completion.get("technical_matches") or [])
        if isinstance(item, dict)
    ]
    proven_matches = [item for item in technical_matches if bool(item.get("proven"))]

    evidence_mapping_exists = any("技术条款证据映射表" in sec.content for sec in sections)
    traced_count, traced_total, trace_missing = _traceability_hits(technical_text, proven_matches)
    trace_ratio = 1.0 if traced_total == 0 else traced_count / traced_total
    evidence_mapping_pass = evidence_mapping_exists and (traced_total == 0 or trace_ratio >= _MIN_PROVEN_COMPLETION_RATE)
    check_items.append(
        {
            "name": "技术条款证据映射",
            "status": "通过" if evidence_mapping_pass else "需修订",
            "detail": (
                "已检测到映射表，且已证实条款均已落入表内"
                if evidence_mapping_pass
                else (
                    "未检测到“技术条款证据映射表”章节内容"
                    if not evidence_mapping_exists
                    else f"已证实条款仅落入 {traced_count}/{traced_total} 项"
                )
            ),
        }
    )
    if not evidence_mapping_pass:
        if not evidence_mapping_exists:
            issues.append("技术章节缺少证据映射表，参数与原文无法一一追溯。")
            suggestions.append("在第三章补充“技术条款证据映射表”，逐条关联招标原文片段。")
        else:
            preview = "；".join(trace_missing[:5]) or "多项已证实条款未落入证据映射表"
            issues.append(f"证据映射表存在但内容未完成：{preview}。")
            suggestions.append("补齐证据映射表中的投标方证据与产品事实，不要只保留招标原文摘录。")

    required_materials = _ensure_str_list(analysis_result.get("required_materials"))
    matched, total, missing = _material_coverage(required_materials, sections)
    coverage_ratio = 1.0 if total == 0 else matched / total
    coverage_pass = coverage_ratio >= 0.6
    check_items.append(
        {
            "name": "资料覆盖率检查",
            "status": "通过" if coverage_pass else "需修订",
            "detail": f"覆盖 {matched}/{total} 项（覆盖率 {coverage_ratio:.0%}）",
        }
    )
    if not coverage_pass and total > 0:
        preview_missing = "；".join(missing[:5]) if missing else "多项资料未覆盖"
        issues.append(f"资料覆盖不足：{preview_missing}。")
        suggestions.append("根据“需准备资料清单”补齐对应章节内容与附件说明。")

    analysis_citations = analysis_result.get("citations")
    if not isinstance(analysis_citations, list):
        analysis_citations = []
    generation_citations: list[dict[str, Any]] = []
    if generation_result and isinstance(generation_result.get("citations"), list):
        generation_citations = generation_result["citations"]

    citation_count = len(analysis_citations) + len(generation_citations)
    citation_pass = citation_count > 0
    check_items.append(
        {
            "name": "检索引用可追溯性",
            "status": "通过" if citation_pass else "需修订",
            "detail": f"可追溯引用条数：{citation_count}",
        }
    )
    if not citation_pass:
        issues.append("未生成检索引用，难以追溯结论依据。")
        suggestions.append("先将招标原文入库并重跑流程，确保输出包含 citations。")

    full_text = "\n".join(sec.content for sec in sections)
    key_info = analysis_result.get("key_information")
    if not isinstance(key_info, dict):
        key_info = {}
    expected_project_name = _safe_text(
        tender.project_name if tender is not None else key_info.get("project_name"),
    )
    expected_project_number = _safe_text(
        tender.project_number if tender is not None else key_info.get("project_number"),
    )

    if expected_project_name or expected_project_number:
        name_ok = not expected_project_name or expected_project_name in full_text
        number_ok = not expected_project_number or expected_project_number in full_text
        identifier_pass = name_ok and number_ok
        detail_bits: list[str] = []
        if expected_project_name:
            detail_bits.append(f"项目名称{'已命中' if name_ok else '未命中'}")
        if expected_project_number:
            detail_bits.append(f"项目编号{'已命中' if number_ok else '未命中'}")
        check_items.append(
            {
                "name": "项目基础信息一致性",
                "status": "通过" if identifier_pass else "需修订",
                "detail": "；".join(detail_bits),
            }
        )
        if not identifier_pass:
            issues.append("项目名称或项目编号未稳定落入正文，存在基础信息不一致风险。")
            suggestions.append("在封面、资格声明和技术/报价章节补入统一的项目名称与项目编号。")

    expected_package_ids = list(selected_packages or [])
    if not expected_package_ids and generation_result:
        expected_package_ids = [str(item) for item in generation_result.get("selected_packages", []) if str(item).strip()]
    if tender is not None:
        pass
    elif isinstance(key_info.get("packages"), list):
        pass

    if expected_package_ids:
        package_mentions = set()
        for match in re.finditer(r"第\s*(\d+)\s*包|包\s*(\d+)", full_text):
            pkg_id = match.group(1) or match.group(2)
            if pkg_id:
                package_mentions.add(pkg_id)
        unexpected_packages = sorted(pkg for pkg in package_mentions if pkg not in set(expected_package_ids))
        package_pass = not unexpected_packages
        check_items.append(
            {
                "name": "包件分仓检查",
                "status": "通过" if package_pass else "需修订",
                "detail": (
                    "未发现串包"
                    if package_pass
                    else f"发现非目标包号：{', '.join(unexpected_packages)}"
                ),
            }
        )
        if not package_pass:
            issues.append(f"正文出现非目标包号：{', '.join(unexpected_packages)}。")
            suggestions.append("按包号重建单包上下文，禁止跨包共享技术条款和报价数据。")

    products = products or {}
    if tender is not None and expected_package_ids:
        compliance_gaps = _product_compliance_gaps(tender, expected_package_ids, products)
        compliance_chain_pass = not compliance_gaps
        check_items.append(
            {
                "name": "行业/货物证明链",
                "status": "通过" if compliance_chain_pass else "需修订",
                "detail": "项目特定证明链已齐备" if compliance_chain_pass else "；".join(compliance_gaps[:5]),
            }
        )
        if not compliance_chain_pass:
            issues.append(f"项目特定证明链不完整：{'；'.join(compliance_gaps[:5])}。")
            suggestions.append("按项目属性补齐注册证、原产地/合法来源、授权链或节能环保认证材料。")

    pollution_hits = [keyword for keyword in _TECH_POLLUTION_KEYWORDS if keyword in technical_text]
    technical_purity_pass = not pollution_hits
    check_items.append(
        {
            "name": "技术章节纯度检查",
            "status": "通过" if technical_purity_pass else "需修订",
            "detail": (
                "未发现评分/合同类污染片段"
                if technical_purity_pass
                else f"命中关键词：{'；'.join(pollution_hits[:5])}"
            ),
        }
    )
    if not technical_purity_pass:
        issues.append("技术章节混入评分办法、合同条款或投诉质疑等非技术内容。")
        suggestions.append("回到条款分类层，限制只有技术条款进入技术偏离表与配置表。")

    response_realization_issues: list[str] = []
    if "承诺满足" in technical_text:
        response_realization_issues.append("技术章节仍存在泛化承诺句式")
    if _PENDING_RESPONSE_TEXT in technical_text:
        response_realization_issues.append("技术表仍存在待核实响应值")
    unresolved_param_placeholders = [
        pattern
        for pattern in ("[品牌型号]", "[生产厂家]", "[品牌]", "[待补充]")
        if pattern in full_text
    ]
    if unresolved_param_placeholders:
        response_realization_issues.append(f"仍存在关键实参占位符：{'；'.join(unresolved_param_placeholders)}")

    response_realization_pass = not response_realization_issues
    check_items.append(
        {
            "name": "技术响应实参化",
            "status": "通过" if response_realization_pass else "需修订",
            "detail": "已使用具体参数/主体信息" if response_realization_pass else "；".join(response_realization_issues),
        }
    )
    if not response_realization_pass:
        issues.append("技术或报价章节仍缺少实参化字段，正文可读性与可提交性不足。")
        suggestions.append("将企业主体、品牌型号、厂家和单价总价注入正式章节后提交。")

    proven_total = len(technical_matches)
    proven_count = int(proven_completion.get("proven_response_count", 0) or 0)
    proven_rate = float(proven_completion.get("proven_completion_rate", 1.0) if proven_total else 1.0)
    completion_pass = proven_total == 0 or proven_rate >= _MIN_PROVEN_COMPLETION_RATE
    check_items.append(
        {
            "name": "已证实响应完成率",
            "status": "通过" if completion_pass else "需修订",
            "detail": (
                "暂无技术要求待计算完成率"
                if proven_total == 0
                else f"已证实 {proven_count}/{proven_total} 项（完成率 {proven_rate:.0%}）"
            ),
        }
    )
    if not completion_pass:
        preview = "；".join(_ensure_str_list(proven_completion.get("unproven_items"))[:5]) or "存在未证实技术要求"
        issues.append(f"技术响应完成率不足：{preview}。")
        suggestions.append("先完成产品事实匹配与投标证据绑定，再输出正式外发版。")

    semantic_issues: list[str] = []
    for binding in technical_matches:
        if not isinstance(binding, dict):
            continue
        parameter_name = _safe_text(binding.get("parameter_name"))
        response_value = _safe_text(binding.get("response_value"))
        if parameter_name and "无偏离" in technical_text and not binding.get("proven"):
            for line in technical_text.splitlines():
                if parameter_name in line and "无偏离" in line:
                    semantic_issues.append(f"{parameter_name} 未证实却标注无偏离")
                    break
        if response_value and parameter_name and parameter_name in technical_text and response_value not in technical_text:
            semantic_issues.append(f"{parameter_name} 已匹配产品事实，但正文未落入响应值")

    semantic_pass = not semantic_issues
    check_items.append(
        {
            "name": "技术响应语义一致性",
            "status": "通过" if semantic_pass else "需修订",
            "detail": "要求、响应值、偏离标注保持一致" if semantic_pass else "；".join(semantic_issues[:5]),
        }
    )
    if not semantic_pass:
        issues.append("技术响应存在语义不一致：要求、响应值或偏离标注未形成闭环。")
        suggestions.append("逐条核对技术偏离表，确保响应值来自已证实产品事实且偏离标注正确。")

    products = products or {}
    if tender is not None and expected_package_ids and products:
        product_gaps: list[str] = []
        for pkg_id in expected_package_ids:
            pkg = next((item for item in tender.packages if item.package_id == pkg_id), None)
            product = products.get(pkg_id)
            if pkg is None or product is None:
                continue
            if product.model.strip() and product.model not in full_text:
                product_gaps.append(f"包{pkg_id} 型号未落正文")
            row_lines = [line for line in full_text.splitlines() if pkg.item_name in line]
            if row_lines and str(pkg.quantity) not in row_lines[0]:
                product_gaps.append(f"包{pkg_id} 数量未在条目行体现")
        product_consistency_pass = not product_gaps
        check_items.append(
            {
                "name": "数量/型号一致性",
                "status": "通过" if product_consistency_pass else "需修订",
                "detail": "数量与型号已落正文" if product_consistency_pass else "；".join(product_gaps),
            }
        )
        if not product_consistency_pass:
            issues.append("报价表或配置表中的型号/数量与选定产品映射未完全对齐。")
            suggestions.append("按包号将产品型号、厂家、数量和价格回填到报价/配置表后复核。")

    # ── 详细度目标校验 ──
    # 检查是否存在叙述性章节
    narrative_keywords = ("关键性能说明", "配置说明", "交付说明", "验收说明", "使用与培训说明")
    found_narratives = [nk for nk in narrative_keywords if nk in full_text]
    narrative_check_pass = len(found_narratives) >= 3
    check_items.append(
        {
            "name": "详细技术响应章节",
            "status": "通过" if narrative_check_pass else "需修订",
            "detail": (
                f"已检测到 {len(found_narratives)}/5 个叙述章节（{', '.join(found_narratives[:3])}）"
                if found_narratives
                else "未检测到详细技术响应章节（关键性能说明/配置说明等）"
            ),
        }
    )
    if not narrative_check_pass:
        issues.append("技术章节缺少详细技术响应说明（关键性能说明/配置说明/交付说明/验收说明/培训说明）。")
        suggestions.append("在技术偏离表后补充关键性能说明、配置说明、交付说明、验收说明和使用与培训说明章节。")

    # 检查偏离表列数（新格式应为8列）
    deviation_8col = "实际响应值" in full_text and "证据材料" in full_text
    check_items.append(
        {
            "name": "偏离表详细度",
            "status": "通过" if deviation_8col else "需修订",
            "detail": "偏离表已升级为8列详细格式" if deviation_8col else "偏离表仍为旧格式，建议升级为8列（含条款编号、投标型号、证据材料、页码、验收备注）",
        }
    )

    # 检查配置表是否含功能描述
    config_desc_present = "配置功能描述" in full_text or "二-B" in full_text
    check_items.append(
        {
            "name": "配置表详细度",
            "status": "通过" if config_desc_present else "需修订",
            "detail": "配置表已包含功能描述层" if config_desc_present else "配置表缺少功能描述层（建议增加配置项用途说明和功能角色描述）",
        }
    )

    # ── 新增5项深度检查（判断"够不够细"而非"有没有"）──

    # (1) offered_fact_coverage: 产品事实覆盖率
    offered_fact_count = 0
    if evidence_result:
        offered_fact_count = int(evidence_result.get("offered_fact_count", 0) or 0)
    tech_req_count = len(technical_matches)
    offered_coverage = offered_fact_count / max(1, tech_req_count) if tech_req_count else 0.0
    offered_coverage = min(offered_coverage, 1.0)
    offered_coverage_pass = offered_coverage >= 0.5
    check_items.append({
        "name": "offered_fact_coverage（产品事实覆盖率）",
        "status": "通过" if offered_coverage_pass else "需修订",
        "detail": f"产品事实覆盖率 {offered_coverage:.0%}（{offered_fact_count} 条事实 / {tech_req_count} 项技术要求）。"
                  + ("" if offered_coverage_pass else " 不足50%，技术表右侧仍大量为待核实。"),
    })
    if not offered_coverage_pass:
        issues.append(f"产品事实覆盖率仅 {offered_coverage:.0%}，技术表中大量参数仍为待核实。")
        suggestions.append("请补充产品彩页、说明书等投标材料，通过 Product Profile Builder 提取真实参数。")

    # (2) bid_evidence_coverage: 投标侧证据页码覆盖率
    bid_evidence_count = 0
    bid_evidence_with_page = 0
    total_bindings = 0
    if evidence_result:
        bid_evidence_count = int(evidence_result.get("bidder_matched_count", 0) or 0)
        total_bindings = int(evidence_result.get("total", 0) or 0)
        # 统计有页码的投标证据数
        bid_evidence_items = evidence_result.get("bid_evidence", [])
        if isinstance(bid_evidence_items, list):
            bid_evidence_with_page = sum(
                1 for item in bid_evidence_items
                if isinstance(item, dict) and item.get("evidence_page") is not None
            )
    bid_ev_coverage = bid_evidence_count / max(1, total_bindings)
    bid_ev_page_coverage = bid_evidence_with_page / max(1, total_bindings)
    bid_ev_pass = bid_ev_coverage >= 0.5
    check_items.append({
        "name": "bid_evidence_coverage（投标方证据覆盖率）",
        "status": "通过" if bid_ev_pass else "需修订",
        "detail": (
            f"投标方证据覆盖率 {bid_ev_coverage:.0%}（{bid_evidence_count}/{total_bindings} 项已绑定），"
            f"含页码 {bid_ev_page_coverage:.0%}（{bid_evidence_with_page}/{total_bindings} 项有页码）。"
        ),
    })
    if not bid_ev_pass:
        issues.append('投标方证据覆盖率不足，证据列仍停留在"待补投标方证据"。')
        suggestions.append("请提供投标材料（彩页/说明书/注册证/检测报告），通过 BidEvidenceBinder 绑定页码。")

    # (3) config_detail_score: 配置项平均条数
    config_rows_per_pkg: dict[str, int] = {}
    current_mode_cfg = ""
    current_pkg_cfg = ""
    for sec in sections:
        for line in sec.content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") and ("配置" in stripped):
                current_mode_cfg = "config"
                pkg_match = re.search(r"第\s*(\d+)\s*包", stripped)
                current_pkg_cfg = pkg_match.group(1) if pkg_match else "?"
                config_rows_per_pkg.setdefault(current_pkg_cfg, 0)
            elif stripped.startswith("#") and current_mode_cfg == "config":
                current_mode_cfg = ""
            elif current_mode_cfg == "config" and stripped.startswith("|") and not stripped.startswith("|---") and not stripped.startswith("| 序号"):
                cells = [c.strip() for c in stripped.split("|")]
                if len(cells) >= 5 and re.match(r"^\d+$", cells[1].strip()):
                    config_rows_per_pkg[current_pkg_cfg] = config_rows_per_pkg.get(current_pkg_cfg, 0) + 1

    avg_config = (
        sum(config_rows_per_pkg.values()) / len(config_rows_per_pkg)
        if config_rows_per_pkg else 0.0
    )
    config_score = min(avg_config / max(1, _DETAIL_TARGETS["config_items_min"]), 1.0)
    config_score_pass = config_score >= 0.8
    cfg_detail_parts = [f"包{pk}:{cnt}项" for pk, cnt in sorted(config_rows_per_pkg.items())]
    check_items.append({
        "name": "config_detail_score（配置详细度评分）",
        "status": "通过" if config_score_pass else "需修订",
        "detail": (
            f"配置详细度 {config_score:.0%}（平均{avg_config:.1f}项/包；{', '.join(cfg_detail_parts) or '无'}）。"
            + ("" if config_score_pass else f" 目标：每包≥{_DETAIL_TARGETS['config_items_min']}项。")
        ),
    })
    if not config_score_pass:
        issues.append(f"配置表过薄（平均{avg_config:.1f}项/包），像模板而非交付清单。")
        suggestions.append("通过 Config Extractor 从投标材料中抽取核心模块、标准附件、配套软件、初始耗材、随机文件、安装/培训资料。")

    # (4) mapping_count_consistency: 技术条款/偏离表/证据映射表行数一致性
    dev_rows_total = 0
    map_rows_total = 0
    for sec in sections:
        in_dev = in_map = False
        for line in sec.content.splitlines():
            stripped = line.strip()
            if "技术偏离" in stripped and stripped.startswith("#"):
                in_dev, in_map = True, False
            elif "证据映射" in stripped and stripped.startswith("#"):
                in_dev, in_map = False, True
            elif stripped.startswith("#"):
                in_dev = in_map = False
            elif stripped.startswith("|") and not stripped.startswith("|---"):
                is_header = any(h in stripped for h in ("条款编号", "序号", "参数名称"))
                if not is_header:
                    if in_dev:
                        dev_rows_total += 1
                    elif in_map:
                        map_rows_total += 1

    mapping_denom = max(1, tech_req_count, dev_rows_total, map_rows_total)
    count_gap = (
        abs(tech_req_count - dev_rows_total)
        + abs(tech_req_count - map_rows_total)
        + abs(dev_rows_total - map_rows_total)
    )
    mc_consistency = max(0.0, 1.0 - count_gap / (3 * mapping_denom))
    mc_pass = mc_consistency >= 0.8
    check_items.append({
        "name": "mapping_count_consistency（表间行数一致性）",
        "status": "通过" if mc_pass else "需修订",
        "detail": f"技术条款 {tech_req_count} 项，偏离表 {dev_rows_total} 行，证据映射表 {map_rows_total} 行（一致性 {mc_consistency:.0%}）。",
    })
    if not mc_pass:
        issues.append("技术条款数、偏离表行数、证据映射表行数不一致，存在遗漏或重复。")
        suggestions.append("确保技术偏离表和证据映射表逐条对应归一化后的技术要求。")

    # (5) section_template_similarity: 模板段落重复率
    _TEMPLATE_MARKERS = (
        "[待填写]", "[品牌型号]", "[生产厂家]", "[品牌]",
        "待核实", "具备完整的技术功能", "配置清单包含主机及全套标准附件",
        "按招标文件配置要求", "详见招标文件",
    )
    content_lines_all = [
        line.strip()
        for sec in sections
        for line in sec.content.splitlines()
        if line.strip()
    ]
    template_hits = sum(
        1 for line in content_lines_all if any(marker in line for marker in _TEMPLATE_MARKERS)
    )
    template_ratio = template_hits / max(1, len(content_lines_all))
    template_pass = template_ratio <= 0.15
    check_items.append({
        "name": "section_template_similarity（模板段落重复率）",
        "status": "通过" if template_pass else "需修订",
        "detail": f"模板化行占比 {template_ratio:.0%}（{template_hits}/{len(content_lines_all)} 行含模板标记）。"
                  + ("" if template_pass else " 超过15%，文档仍像长模板而非项目化说明。"),
    })
    if not template_pass:
        issues.append(f"模板段落重复率 {template_ratio:.0%}，文档内容过于模板化。")
        suggestions.append("切换到 Rich draft mode，引用本包真实参数、配置和证据替换模板句。")

    # ── 底稿完整性检查（5项新增）──

    # (6) 条款数检查 — 若某包 < 5 条 requirement 则告警
    if tender is not None:
        thin_packages: list[str] = []
        pkg_req_counts: dict[str, int] = {}
        for pkg in tender.packages:
            req_count = len(pkg.technical_requirements or {})
            pkg_req_counts[pkg.package_id] = req_count
            if req_count < 5:
                thin_packages.append(f"包{pkg.package_id}({req_count}条)")
        clause_count_pass = not thin_packages
        check_items.append({
            "name": "条款数充足性",
            "status": "通过" if clause_count_pass else "需修订",
            "detail": (
                "各包条款数均≥5条"
                if clause_count_pass
                else f"条款数不足：{'；'.join(thin_packages)}"
            ),
        })
        if not clause_count_pass:
            issues.append(f"以下包条款数过少（<5条）：{'；'.join(thin_packages)}。底稿拆条不够细。")
            suggestions.append("对条款数不足的包，从招标原文中补充提取技术参数，确保每包至少5条原子级条款。")

        # (7) 包间粒度均匀性 — 最多包/最少包差异超过3倍则告警
        if len(pkg_req_counts) >= 2:
            max_count = max(pkg_req_counts.values())
            min_count = max(1, min(pkg_req_counts.values()))
            granularity_ratio = max_count / min_count
            granularity_pass = granularity_ratio <= 3.0
            check_items.append({
                "name": "包间粒度均匀性",
                "status": "通过" if granularity_pass else "需修订",
                "detail": (
                    f"包间条款数比 {granularity_ratio:.1f}:1（"
                    + "、".join(f"包{k}:{v}条" for k, v in sorted(pkg_req_counts.items()))
                    + "）"
                ),
            })
            if not granularity_pass:
                issues.append(f"包间拆分粒度不均匀（比值{granularity_ratio:.1f}:1），部分包过粗。")
                suggestions.append("对条款数较少的包从原文重新提取，使各包粒度差距不超过3倍。")

    # (8) 配置表薄弱检查 — 配置项 < 3 则告警（仅在有明确包号时检查）
    meaningful_config_pkgs = {k: v for k, v in config_rows_per_pkg.items() if k != "?"}
    thin_config_packages: list[str] = []
    for pkg_id, count in meaningful_config_pkgs.items():
        if count < 3:
            thin_config_packages.append(f"包{pkg_id}({count}项)")
    if meaningful_config_pkgs:
        config_thin_pass = not thin_config_packages
        check_items.append({
            "name": "配置表薄弱检查",
            "status": "通过" if config_thin_pass else "需修订",
            "detail": (
                "各包配置项均≥3项"
                if config_thin_pass
                else f"配置表过薄：{'；'.join(thin_config_packages)}"
            ),
        })
        if not config_thin_pass:
            issues.append(f"配置表过薄：{'；'.join(thin_config_packages)}，缺少核心模块/附件/软件等分类。")
            suggestions.append("补充配置表至少覆盖6大类别：核心模块、标准附件、配套软件、初始耗材、随机文件、安装/培训资料。")

    # (9) 原文片段污染检查 — 证据映射表中的片段含评分/商务等非技术内容
    polluted_snippets: list[str] = []
    _SNIPPET_POLLUTION_KEYWORDS = ("评分标准", "评分办法", "商务条款", "合同条款",
                                    "违约责任", "质疑", "投诉", "付款方式")
    for sec in sections:
        if "证据映射" not in sec.section_title and "证据映射" not in sec.content[:200]:
            continue
        for line in sec.content.splitlines():
            if not line.strip().startswith("|"):
                continue
            for kw in _SNIPPET_POLLUTION_KEYWORDS:
                if kw in line:
                    cells = [c.strip() for c in line.split("|") if c.strip()]
                    param_name = cells[1] if len(cells) > 1 else "未知"
                    polluted_snippets.append(f"{param_name}含'{kw}'")
                    break
    snippet_purity_pass = not polluted_snippets
    check_items.append({
        "name": "原文片段污染检查",
        "status": "通过" if snippet_purity_pass else "需修订",
        "detail": (
            "证据映射表原文片段未发现非技术内容污染"
            if snippet_purity_pass
            else f"原文片段污染：{'；'.join(polluted_snippets[:5])}"
        ),
    })
    if not polluted_snippets:
        pass
    else:
        issues.append(f"证据映射表中有{len(polluted_snippets)}处原文片段混入了非技术内容。")
        suggestions.append("回到原文切割层，确保证据片段只包含技术参数相关内容，过滤评分/商务/合同条款。")

    overall_status = "通过" if not issues else "需修订"
    if not suggestions and overall_status == "通过":
        suggestions = ["可进入人工终审与盖章提交流程。"]

    summary = (
        f"二次校验完成：{len(check_items)} 项，"
        f"{'全部通过' if overall_status == '通过' else f'发现 {len(issues)} 项问题'}。"
    )

    return {
        "executed": True,
        "overall_status": overall_status,
        "check_items": check_items,
        "issues": issues,
        "suggestions": suggestions,
        "proven_completion": {
            "proven_count": proven_count,
            "total": proven_total,
            "rate": round(proven_rate, 4) if proven_total else 1.0,
            "unproven_items": _ensure_str_list(proven_completion.get("unproven_items")),
        },
        "summary": summary,
    }


def _append_unique(base: list[str], extras: list[str]) -> list[str]:
    for item in extras:
        normalized = str(item).strip()
        if normalized and normalized not in base:
            base.append(normalized)
    return base


def _format_eval_rules(evaluation_criteria: dict[str, Any]) -> list[str]:
    if not evaluation_criteria:
        return []
    rules: list[str] = []
    for k, v in evaluation_criteria.items():
        if isinstance(v, (int, float)):
            rules.append(f"{k}：{v}")
        else:
            rules.append(f"{k}：{v}")
    return rules


def _default_required_materials(tender: TenderDocument) -> list[str]:
    materials = [
        "营业执照及法定代表人身份证明",
        "法定代表人授权书及授权代表身份证明",
        "供应商资格承诺函（含政府采购法第二十二条相关承诺）",
        "依法缴纳税收和社保证明材料",
        "信用记录查询截图（信用中国/中国政府采购网等）",
        "报价书、报价一览表、报价明细表",
        "技术偏离表及详细配置明细表",
        "技术服务与售后服务方案",
    ]
    joined = " ".join(pkg.item_name for pkg in tender.packages if pkg.item_name.strip())
    if "医疗器械" in joined or "流式" in joined:
        materials.extend(
            [
                "医疗器械经营许可证/备案凭证（如适用）",
                "产品注册证/备案证明（如适用）",
                "厂家授权书（如适用）",
            ]
        )
    # 去重并保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for item in materials:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _default_step1_result(tender: TenderDocument) -> dict[str, Any]:
    packages = [
        {
            "package_id": pkg.package_id,
            "item_name": pkg.item_name,
            "quantity": pkg.quantity,
            "budget": pkg.budget,
        }
        for pkg in tender.packages
    ]
    return {
        "key_information": {
            "project_name": tender.project_name,
            "project_number": tender.project_number,
            "purchaser": tender.purchaser,
            "agency": tender.agency,
            "procurement_type": tender.procurement_type,
            "budget": tender.budget,
            "packages": packages,
            "commercial_terms": tender.commercial_terms.model_dump(),
        },
        "required_materials": _default_required_materials(tender),
        "offered_facts": [],
        "scoring_rules": _format_eval_rules(tender.evaluation_criteria),
        "risk_alerts": [
            "请重点核对投标有效期、交货期限和履约保证金条款。",
            "请确保技术参数响应表逐条对应，不要遗漏关键参数。",
            "证照与授权文件需在有效期内，且与投标产品一致。",
        ],
        "citations": [],
        "summary": "已完成招标关键信息、资料清单和评分规则提取。",
    }


def _material_item(item: str, status: str, evidence: str, suggestion: str = "") -> dict[str, str]:
    return {
        "item": item,
        "status": status,
        "evidence": evidence,
        "suggestion": suggestion,
    }


def _default_step4_if_blocked(reason: str) -> dict[str, Any]:
    return {
        "ready_for_submission": False,
        "risk_level": "high",
        "compliance_score": 0.0,
        "major_issues": [reason],
        "recommendations": ["先补齐资料缺口，再重新运行第三步与第四步。"],
        "secondary_validation": {
            "executed": False,
            "overall_status": "需修订",
            "check_items": [],
            "issues": [reason],
            "suggestions": ["先补齐资料缺口后再执行二次校验。"],
            "summary": "未执行二次校验：缺少可审核标书内容。",
        },
        "conclusion": "当前不具备提交条件。",
    }
