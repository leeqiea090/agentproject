from __future__ import annotations

import app.services.tender_workflow.common as _common

def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common,):
    __reexport_all(_module)

del _module
def _classify_clauses(
    tender: TenderDocument,
    analysis_result: dict[str, Any],
    selected_packages: list[str],
    raw_text: str,
) -> dict[str, Any]:
    """分类条款。"""
    required_materials = _ensure_str_list(analysis_result.get("required_materials"))
    scoring_rules = _ensure_str_list(analysis_result.get("scoring_rules"))
    target_packages = selected_packages or [pkg.package_id for pkg in tender.packages]
    package_map = {pkg.package_id: pkg for pkg in tender.packages}
    target_package_docs = [package_map[pkg_id] for pkg_id in target_packages if pkg_id in package_map]
    context = _workflow_context_text(tender)

    qualification_clauses = required_materials[:8]
    technical_clauses: list[str] = []
    commercial_clauses: list[str] = []
    for pkg in target_package_docs:
        for key, value in (pkg.technical_requirements or {}).items():
            technical_clauses.append(f"{key}：{value}")
        commercial_clauses.append(f"包{pkg.package_id} 交货期：{pkg.delivery_time or '按招标文件约定'}")
        commercial_clauses.append(f"包{pkg.package_id} 交货地点：{pkg.delivery_place or '采购人指定地点'}")

    commercial_terms = tender.commercial_terms.model_dump()
    for key, value in commercial_terms.items():
        if value:
            commercial_clauses.append(f"{key}：{value}")

    policy_clauses = scoring_rules[:6]
    special_requirements = tender.special_requirements.strip()
    if special_requirements:
        policy_clauses.append(special_requirements)

    if "不接受联合体" in context:
        consortium_decision = "不接受联合体"
    elif _contains_any(context, _CONSORTIUM_KEYWORDS):
        consortium_decision = "接受联合体"
    else:
        consortium_decision = "未明确，需人工确认"

    if _contains_any(context, _SME_KEYWORDS):
        sme_decision = "适用中小企业政策分支"
    else:
        sme_decision = "不涉及中小企业政策分支"

    if _contains_any(context, _MEDICAL_KEYWORDS):
        medical_decision = "需走医疗器械合规分支"
    else:
        medical_decision = "非医疗专项分支"

    if _contains_any(context, _IMPORTED_KEYWORDS):
        imported_decision = "需准备进口或原产地相关说明"
    else:
        imported_decision = "默认按国产/未明确处理"

    package_decision = "、".join(f"包{pkg.package_id}" for pkg in target_package_docs) or "全部包"
    branch_decisions = [
        _branch_decision(
            "联合体投标分支",
            consortium_decision,
            _snippet_around(raw_text, "联合体") or special_requirements or "依据结构化招标信息推断",
            "资格/组织形式",
        ),
        _branch_decision(
            "中小企业政策分支",
            sme_decision,
            _snippet_around(raw_text, "中小企业") or special_requirements or "依据评分标准及特殊要求推断",
            "政策性条款",
        ),
        _branch_decision(
            "医疗器械合规分支",
            medical_decision,
            _snippet_around(raw_text, "医疗器械") or " ".join(pkg.item_name for pkg in target_package_docs) or "依据采购标的推断",
            "行业合规",
        ),
        _branch_decision(
            "进口货物分支",
            imported_decision,
            _snippet_around(raw_text, "进口") or " ".join(pkg.item_name for pkg in target_package_docs) or "依据采购标的推断",
            "货物属性",
        ),
        _branch_decision(
            "包件响应分支",
            f"本次响应范围：{package_decision}",
            package_decision,
            "投标范围",
        ),
    ]

    summary = (
        f"已完成条款分类，覆盖资格、技术、配置、服务、验收、商务 6 类 + 噪音；"
        f"已生成 {len(branch_decisions)} 条分支决策。"
    )

    return {
        "selected_packages": target_packages,
        "package_count": len(target_package_docs),
        "clause_categories": {
            "qualification": qualification_clauses,
            "technical": technical_clauses[:20],
            "technical_requirement": [c for c in technical_clauses[:20] if not _is_service_or_acceptance_clause(c)],
            "config_requirement": [c for c in technical_clauses[:20] if _is_config_clause(c)],
            "service_requirement": [c for c in technical_clauses[:20] if _is_service_clause(c)],
            "acceptance_requirement": [c for c in technical_clauses[:20] if _is_acceptance_clause(c)],
            "commercial": commercial_clauses[:12],
            "commercial_requirement": commercial_clauses[:12],
            "policy": policy_clauses[:12],
            "noise": [],
        },
        "structured_categories": {
            "procurement_requirements": qualification_clauses + technical_clauses[:20] + commercial_clauses[:12],
            "evaluation_rules": policy_clauses[:12],
            "evidence_materials": qualification_clauses,
            "explanatory_clauses": [special_requirements] if special_requirements else [],
        },
        "branch_decisions": branch_decisions,
        "summary": summary,
    }


def _normalize_requirements(
    tender: TenderDocument,
    analysis_result: dict[str, Any],
    clause_result: dict[str, Any],
    selected_packages: list[str],
    raw_text: str,
) -> dict[str, Any]:
    """归一化需求。"""
    target_package_ids = selected_packages or [pkg.package_id for pkg in tender.packages]
    package_map = {pkg.package_id: pkg for pkg in tender.packages}
    target_packages = [package_map[pkg_id] for pkg_id in target_package_ids if pkg_id in package_map]

    qualification_requirements: list[dict[str, Any]] = []
    for idx, item in enumerate(_ensure_str_list(analysis_result.get("required_materials")), start=1):
        qualification_requirements.append(
            {
                "requirement_id": f"Q-{idx}",
                "requirement_type": _qualification_requirement_type(item),
                "requirement_text": item,
                "source_excerpt": _locate_evidence_snippet(raw_text, item),
            }
        )

    commercial_requirements: list[dict[str, Any]] = []
    for pkg in target_packages:
        commercial_requirements.extend(
            [
                {
                    "requirement_id": f"B-{pkg.package_id}-delivery-time",
                    "package_id": pkg.package_id,
                    "field": "delivery_time",
                    "value": _safe_text(pkg.delivery_time, "按招标文件约定"),
                    "source_excerpt": _locate_evidence_snippet(raw_text, pkg.delivery_time or "交货期"),
                },
                {
                    "requirement_id": f"B-{pkg.package_id}-delivery-place",
                    "package_id": pkg.package_id,
                    "field": "delivery_place",
                    "value": _safe_text(pkg.delivery_place, "采购人指定地点"),
                    "source_excerpt": _locate_evidence_snippet(raw_text, pkg.delivery_place or "交货地点"),
                },
                {
                    "requirement_id": f"B-{pkg.package_id}-quantity",
                    "package_id": pkg.package_id,
                    "field": "quantity",
                    "value": pkg.quantity,
                    "source_excerpt": _locate_evidence_snippet(raw_text, str(pkg.quantity)),
                },
                {
                    "requirement_id": f"B-{pkg.package_id}-budget",
                    "package_id": pkg.package_id,
                    "field": "budget",
                    "value": pkg.budget,
                    "source_excerpt": _locate_evidence_snippet(raw_text, str(int(pkg.budget)) if pkg.budget else "预算"),
                },
            ]
        )

    term_map = tender.commercial_terms.model_dump()
    for field, value in term_map.items():
        if value:
            commercial_requirements.append(
                {
                    "requirement_id": f"B-common-{field}",
                    "package_id": "common",
                    "field": field,
                    "value": value,
                    "source_excerpt": _locate_evidence_snippet(raw_text, str(value)),
                }
            )

    technical_requirements: list[dict[str, Any]] = []
    for pkg in target_packages:
        tech_items = list((pkg.technical_requirements or {}).items())
        if not tech_items:
            for clause in _ensure_str_list(clause_result.get("clause_categories", {}).get("technical")):
                if "：" not in clause:
                    continue
                key, value = clause.split("：", 1)
                tech_items.append((key.strip(), value.strip()))

        # --- Fix: detect sparse/collapsed requirements and use raw-text extraction ---
        _is_sparse = (
            len(tech_items) < 3
            or all(k.strip() in _GENERIC_TECH_KEYS for k, _ in tech_items)
            or all(_is_generic_value(v) for _, v in tech_items)
            or (len(tech_items) < 6 and any(_is_generic_value(v) for _, v in tech_items))
        )
        if _is_sparse and raw_text.strip():
            try:
                effective = _effective_requirements(pkg, raw_text)
                if len(effective) > len(tech_items):
                    existing_keys = {k.strip() for k, _ in tech_items}
                    merged = list(tech_items)
                    for ek, ev in effective:
                        if ek.strip() not in existing_keys:
                            merged.append((ek, ev))
                            existing_keys.add(ek.strip())
                    tech_items = merged
                    logger.info(
                        "包%s 需求归一化：原始条目稀疏，通过原文提取补充至 %d 条",
                        pkg.package_id,
                        len(tech_items),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("包%s 原文需求补充提取失败：%s", pkg.package_id, exc)
        # --- End fix ---

        # --- 原子化拆分：将复合条款拆分为原子级技术要求 ---
        try:
            atomized = _atomize_requirements(tech_items)
            if len(atomized) > len(tech_items):
                logger.info(
                    "包%s 原子化拆分：从 %d 条扩展为 %d 条原子级技术要求",
                    pkg.package_id,
                    len(tech_items),
                    len(atomized),
                )
            tech_items = atomized
        except Exception as exc:  # noqa: BLE001
            logger.warning("包%s 原子化拆分失败：%s", pkg.package_id, exc)
        # --- End atomization ---

        # --- 过滤总括条款：禁止笼统概述直接进入最终技术表 ---
        _BLANKET_PATTERNS = (
            "满足招标文件所有要求",
            "符合国家标准",
            "按招标文件要求提供",
            "详见招标文件",
            "满足采购文件全部要求",
            "完全响应招标文件",
            "以上参数均满足",
            "所有技术参数满足",
        )
        filtered_tech_items: list[tuple[str, str]] = []
        for key, value in tech_items:
            combined = f"{key} {value}"
            # 跳过纯总括性条款
            if any(bp in combined for bp in _BLANKET_PATTERNS) and len(value.strip()) < 30:
                logger.debug("包%s 过滤总括条款：%s = %s", pkg.package_id, key, value)
                continue
            filtered_tech_items.append((key, value))
        tech_items = filtered_tech_items
        # --- End blanket clause filter ---

        # --- 跨包污染过滤：命中别包关键词时判噪音 ---
        other_pkg_keywords: dict[str, list[str]] = {}
        for other_pkg in target_packages:
            if other_pkg.package_id == pkg.package_id:
                continue
            name_tokens = re.split(r'[/、,，\s]+', other_pkg.item_name)
            other_pkg_keywords[other_pkg.package_id] = [t for t in name_tokens if len(t) >= 2]

        cross_pkg_filtered: list[tuple[str, str]] = []
        for key, value in tech_items:
            combined = f"{key} {value}"
            contaminated = False
            for other_id, keywords in other_pkg_keywords.items():
                if any(kw in combined for kw in keywords):
                    logger.info("包%s 跨包污染过滤：'%s' 命中包%s关键词，已移除", pkg.package_id, key, other_id)
                    contaminated = True
                    break
            if not contaminated:
                cross_pkg_filtered.append((key, value))
        tech_items = cross_pkg_filtered
        # --- End cross-package contamination filter ---

        for idx, (key, value) in enumerate(tech_items, start=1):
            comparator, threshold, unit = _parse_threshold(_safe_text(value))
            # 增强: 提取 source_page 和 source_text
            source_excerpt = _locate_evidence_snippet(raw_text, key) or _locate_evidence_snippet(raw_text, str(value))
            source_page = None  # 如果 raw_text 包含页码信息可以提取
            # 尝试从 source_excerpt 中提取页码 (格式: "...第X页...")
            if source_excerpt:
                page_match = re.search(r"第\s*(\d+)\s*页", source_excerpt)
                if page_match:
                    source_page = int(page_match.group(1))

            # 条款分类
            combined_text = f"{key} {value}"
            if _is_service_clause(combined_text):
                req_category = "service_requirement"
            elif _is_acceptance_clause(combined_text):
                req_category = "acceptance_requirement"
            elif _is_config_clause(combined_text):
                req_category = "config_requirement"
            else:
                req_category = "technical_requirement"

            technical_requirements.append(
                {
                    "requirement_id": f"T-{pkg.package_id}-{idx}",
                    "package_id": pkg.package_id,
                    "clause_no": f"{pkg.package_id}.{idx}",
                    "param_name": key,
                    "parameter_name": key,
                    "operator": comparator or "",
                    "comparator": comparator,
                    "threshold": threshold or "",
                    "unit": unit or "",
                    "normalized_value": _safe_text(value),
                    "response_field_hint": key,
                    "response_value_type": "numeric" if comparator and threshold else "text",
                    "is_material_requirement": _looks_material_requirement(f"{key} {value}"),
                    "is_material": _looks_material_requirement(f"{key} {value}"),
                    "category": req_category,
                    "source_page": source_page,
                    "source_text": source_excerpt or "",
                    "source_excerpt": source_excerpt or "",
                }
            )

    by_package = {
        pkg.package_id: {
            "item_name": pkg.item_name,
            "technical_count": len([item for item in technical_requirements if item["package_id"] == pkg.package_id]),
            "commercial_count": len([item for item in commercial_requirements if item["package_id"] in {pkg.package_id, "common"}]),
        }
        for pkg in target_packages
    }

    summary = (
        f"需求归一化完成：资格 {len(qualification_requirements)} 项，"
        f"商务 {len(commercial_requirements)} 项，技术 {len(technical_requirements)} 项。"
    )
    # 按 category 分组技术要求（用于分表输出）
    tech_by_category: dict[str, list[dict[str, Any]]] = {}
    for req in technical_requirements:
        cat = req.get("category", "technical_requirement")
        tech_by_category.setdefault(cat, []).append(req)
    return {
        "selected_packages": target_package_ids,
        "qualification_requirements": qualification_requirements,
        "commercial_requirements": commercial_requirements,
        "technical_requirements": technical_requirements,
        "tech_by_category": tech_by_category,
        "by_package": by_package,
        "summary": summary,
    }


def _expand_extracted_facts(
    normalized_result: dict[str, Any],
    products: dict[str, ProductSpecification],
    tender: TenderDocument,
) -> dict[str, Any]:
    """Detail Expander：将已抽到的事实展开成详细响应。

    对每个技术要求，结合产品信息生成 2-3 句详细响应说明，
    包含：要求含义、产品响应方式、证据基础。
    """
    expanded_items: list[dict[str, Any]] = []
    expanded_count = 0

    for requirement in normalized_result.get("technical_requirements", []):
        if not isinstance(requirement, dict):
            continue

        package_id = _safe_text(requirement.get("package_id"))
        parameter_name = _safe_text(requirement.get("parameter_name"))
        normalized_value = _safe_text(requirement.get("normalized_value"))
        product = products.get(package_id)

        # 生成详细响应
        detail_lines: list[str] = [f"本条款要求{parameter_name}满足「{normalized_value}」。"]

        # 1. 要求含义

        # 2. 产品响应方式
        if product:
            matched_value = _lookup_product_spec_value(product, parameter_name)
            if matched_value:
                detail_lines.append(
                    f"投标产品（{product.manufacturer or ''} {product.product_name}，"
                    f"型号：{product.model or '详见偏离表'}）的{parameter_name}为{matched_value}，"
                    "满足招标要求。"
                )
                expanded_count += 1
            else:
                # 能力类推断
                _CAP = ("具备", "支持", "提供", "配备", "配置", "满足", "可", "能够")
                if any(m in f"{parameter_name}{normalized_value}" for m in _CAP):
                    detail_lines.append(
                        f"投标产品（{product.product_name}）具备该功能，满足本条款要求。"
                    )
                    expanded_count += 1
                elif product.product_name.strip():
                    detail_lines.append(
                        f"投标产品（{product.product_name}）满足本条款要求，"
                        "详见技术偏离表对应行。"
                    )
                    expanded_count += 1

            # 3. 证据基础
            if product.registration_number:
                detail_lines.append(f"证据依据：产品注册证编号 {product.registration_number}。")
            elif product.certifications:
                detail_lines.append(f"证据依据：已取得{product.certifications[0]}等认证。")
            elif matched_value:
                detail_lines.append("证据依据：产品参数库已验证数据。")
        else:
            detail_lines.append("（尚未绑定投标产品，待补充产品信息后展开响应。）")

        expanded_items.append({
            "requirement_id": requirement.get("requirement_id"),
            "package_id": package_id,
            "parameter_name": parameter_name,
            "normalized_value": normalized_value,
            "detail_expansion": " ".join(detail_lines),
            "expanded": expanded_count > 0,
        })

    total = len(expanded_items)
    expansion_rate = expanded_count / max(1, total)

    return {
        "expanded_items": expanded_items,
        "expanded_count": expanded_count,
        "total": total,
        "expansion_rate": round(expansion_rate, 4),
        "summary": (
            f"详细展开完成：{expanded_count}/{total} 项已展开为详细响应说明"
            f"（展开率 {expansion_rate:.0%}）。"
        ),
    }
