from __future__ import annotations

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.table_builders as _table_builders
import app.services.one_click_generator.qualification_sections as _qualification_sections
import app.services.evidence_binder as _evidence_binder
import app.services.quality_gate as _quality_gate
import app.services.requirement_processor as _requirement_processor

def __reexport_all(module) -> None:
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
    draft_mode: str = "rich",
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
        delivery_time = _safe_text(pkg.delivery_time, "按招标文件约定")
    if pkg.delivery_place:
        delivery_place = _safe_text(pkg.delivery_place, "采购人指定地点")

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
            f"综上，{p_name}的核心性能指标均满足或优于采购文件技术要求，"
            "能够有效支撑采购人的日常业务需求。"
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
        qty = _infer_package_quantity(pkg, tender_raw)
        config_lines.extend([
            f"1. **核心模块**：{p_name}主机{qty}台（套），"
            f"品牌{p_brand or p_mfr or '[待填写]'}，型号{p_model or '[待填写]'}。",
            "2. **配套软件**：随机提供设备运行所需的全套软件系统，包括数据采集、"
            "分析处理和报告管理模块。",
            "3. **标准附件与初始耗材**：按采购文件配置清单提供全部标准附件、"
            "随机工具和初始耗材。",
            "4. **随机文件**：提供设备使用说明书、合格证、装箱单及相关技术文件。",
            "5. **安装/培训资料**：提供安装指导手册和操作培训教材。",
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


def _generate_rich_draft_sections(
    tender: TenderDocument,
    products: dict,
    *,
    draft_mode: str = "rich",
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    active_packages: list[ProcurementPackage] | None = None,
) -> list[BidDocumentSection]:
    """双模式 Section Writer — 底稿优化版，固定分表输出。

    writer 输入必须是 requirement + package_context + table_type。
    固定分表输出：
    1. 技术参数响应表（仅 technical_requirement）
    2. 配置清单（仅 config_requirement）
    3. 售后服务响应表（仅 service_requirement）
    4. 验收/资料要求响应表（acceptance + documentation）

    每个包独立生成，只消费同包对象。
    """
    sections: list[BidDocumentSection] = []
    is_rich = draft_mode == "rich"
    normalized_reqs = normalized_reqs or {}
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

        # ── 分表1: 技术参数响应表 ──
        tech_reqs = [r for r in pkg_reqs if r.category == ClauseCategory.technical_requirement]
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
            tech_content += "[TODO:待补技术参数响应表]\n"
        tech_content += "\n"

        # ── 分表2: 配置清单 ──
        config_reqs = [r for r in pkg_reqs if r.category == ClauseCategory.config_requirement]
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
                config_content += f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | [TODO:待补] |\n"
        else:
            config_content += "[TODO:待补配置清单]\n"
        config_content += "\n"

        # ── 分表3: 售后服务响应表 ──
        service_reqs = [r for r in pkg_reqs if r.category == ClauseCategory.service_requirement]
        service_content = f"### 包{pkg.package_id} 售后服务响应表\n\n"
        warranty = tender.commercial_terms.warranty_period
        service_content += f"- 质保期：{warranty or '[TODO:待补质保期限]'}\n"
        service_content += "- 响应时间：接到通知后24小时内响应\n"
        service_content += "- 维修服务：提供7×24小时技术热线\n\n"
        if service_reqs:
            service_content += "| 序号 | 服务项 | 招标要求 | 投标承诺 |\n"
            service_content += "|------|--------|----------|----------|\n"
            for idx, req in enumerate(service_reqs, 1):
                service_content += f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | 满足 |\n"
        service_content += "\n"

        # ── 分表4: 验收/资料要求响应表 ──
        accept_reqs = [r for r in pkg_reqs if r.category in (
            ClauseCategory.acceptance_requirement,
            ClauseCategory.documentation_requirement,
        )]
        accept_content = f"### 包{pkg.package_id} 验收及资料要求响应表\n\n"
        if accept_reqs:
            accept_content += "| 序号 | 类型 | 要求项 | 招标要求 | 投标响应 |\n"
            accept_content += "|------|------|--------|----------|----------|\n"
            for idx, req in enumerate(accept_reqs, 1):
                cat_label = "验收" if req.category == ClauseCategory.acceptance_requirement else "资料"
                accept_content += f"| {idx} | {cat_label} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | 满足 |\n"
        else:
            accept_content += "- 验收标准：按照国家标准及采购文件要求\n"
            accept_content += "- 随机文件：[TODO:待补随机文件清单]\n"
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
        content += f"本包投标产品为{p_name}。[TODO:待补品牌型号及厂家信息]\n\n"

    if specs:
        content += "**核心技术参数**：\n\n"
        for idx, (key, value) in enumerate(list(specs.items())[:10], start=1):
            content += f"{idx}. **{key}**：{_as_text(value)}。\n"
        content += "\n"
    else:
        content += "[TODO:待补关键性能参数]\n\n"

    # 交付说明
    content += f"### 包{pkg.package_id} 交付说明\n\n"
    content += f"- 交货期：{pkg.delivery_time or '[TODO:待补交货期限]'}\n"
    content += f"- 交货地点：{pkg.delivery_place or '[TODO:待补交货地点]'}\n"
    content += "- 交货方式：由我公司负责运输至指定地点\n\n"

    # 培训说明
    content += f"### 包{pkg.package_id} 使用与培训说明\n\n"
    content += f"- 培训对象：{pkg.item_name}操作人员、维护人员\n"
    content += "- 培训方式：现场培训+远程技术支持\n"
    content += "- 培训时长：不少于3天 [TODO:待核实具体培训时长要求]\n"
    warranty = tender.commercial_terms.warranty_period
    content += f"- 质保期：{warranty or '[TODO:待补质保期限]'}\n\n"

    return content


def _gen_technical(
    llm: ChatOpenAI,
    tender: TenderDocument,
    tender_raw: str,
    products: dict | None = None,
        *,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, dict[str, Any]] | None = None,
) -> BidDocumentSection:
    """第三章：商务及技术部分 — 增强版：分类分表"""
    _ = llm
    products = products or {}
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    package_details = _package_detail_lines(tender, tender_raw)
    quote_table = _quote_overview_table(tender, tender_raw)

    technical_sections: list[str] = []
    service_sections: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            product = products.get(pkg.package_id)
            product_profile = (product_profiles or {}).get(pkg.package_id)
            requirement_rows, total_requirements = _build_requirement_rows(
                pkg,
                tender_raw,
                product=product,
                normalized_result=normalized_result,
                evidence_result=evidence_result,
                product_profile=product_profile,
            )

            # ── 条款分类过滤：只有技术类进主表 ──
            tech_rows = []
            svc_rows = []
            config_rows_list = []
            acceptance_rows = []
            doc_rows = []
            for row in requirement_rows:
                cat = _classify_clause_category(
                    row.get("key", ""), row.get("value", "")
                )
                if cat == ClauseCategory.service_requirement:
                    svc_rows.append(row)
                elif cat == ClauseCategory.config_requirement:
                    config_rows_list.append(row)
                elif cat == ClauseCategory.acceptance_requirement:
                    acceptance_rows.append(row)
                elif cat == ClauseCategory.documentation_requirement:
                    doc_rows.append(row)
                elif cat in (ClauseCategory.commercial_requirement,
                             ClauseCategory.compliance_note,
                             ClauseCategory.attachment_requirement,
                             ClauseCategory.noise):
                    pass  # 不进任何技术表
                else:
                    tech_rows.append(row)

            # ── 技术偏离表最小行数检查 ──
            min_rows = _DETAIL_TARGETS.get("deviation_table_min_rows", 10)
            if len(tech_rows) < min_rows:
                logger.warning(
                    "包%s 技术偏离表行数不足: %d < %d (最小要求)，建议补充技术条款",
                    pkg.package_id, len(tech_rows), min_rows,
                )

            mapped_count = sum(1 for row in tech_rows if bool(row.get("mapped")))

            technical_sections.append(f"### 包{pkg.package_id}：{pkg.item_name}")
            technical_sections.append(
                _build_deviation_table(
                    tender=tender,
                    pkg=pkg,
                    requirement_rows=tech_rows,
                    total_requirements=len(tech_rows),
                    product=product,
                )
            )
            technical_sections.append(
                _build_configuration_table(
                    pkg,
                    tender_raw,
                    product=product,
                    product_profile=product_profile,
                )
            )
            technical_sections.append(
                _build_response_checklist_table(
                    pkg=pkg,
                    mapped_count=mapped_count,
                    total_requirements=len(tech_rows),
                    requirement_rows=tech_rows,
                )
            )
            technical_sections.append(
                _build_evidence_mapping_table(
                    pkg=pkg,
                    requirement_rows=tech_rows,
                    total_requirements=len(tech_rows),
                )
            )
            # ── 详细技术响应说明章节（post-table narratives）──
            technical_sections.append(
                _build_post_table_narratives(
                    pkg=pkg,
                    tender=tender,
                    tender_raw=tender_raw,
                    product=product,
                    requirement_rows=tech_rows,
                )
            )

            # ── 售后服务要求分表 ──
            if svc_rows:
                svc_table_lines = [
                    f"### 包{pkg.package_id} 售后服务要求响应表",
                    "| 序号 | 售后服务要求 | 响应承诺 | 证据材料 |",
                    "|---:|---|---|---|",
                ]
                for idx, row in enumerate(svc_rows, start=1):
                    key = _markdown_cell(row.get("key", ""))
                    val = _markdown_cell(row.get("value", ""))
                    resp = _markdown_cell(row.get("response", "满足"))
                    evidence = _markdown_cell(row.get("evidence", "详见售后服务方案"))
                    svc_table_lines.append(f"| {idx} | {key}：{val} | {resp} | {evidence} |")
                service_sections.append("\n".join(svc_table_lines))

            # ── 配置类要求分表（如原先未在配置表中处理） ──
            if config_rows_list:
                cfg_table_lines = [
                    f"### 包{pkg.package_id} 配置要求响应表",
                    "| 序号 | 配置要求 | 响应情况 | 证据材料 |",
                    "|---:|---|---|---|",
                ]
                for idx, row in enumerate(config_rows_list, start=1):
                    key = _markdown_cell(row.get("key", ""))
                    val = _markdown_cell(row.get("value", ""))
                    resp = _markdown_cell(row.get("response", "满足"))
                    evidence = _markdown_cell(row.get("evidence", "详见配置清单"))
                    cfg_table_lines.append(f"| {idx} | {key}：{val} | {resp} | {evidence} |")
                service_sections.append("\n".join(cfg_table_lines))

            # ── 验收要求分表 ──
            if acceptance_rows:
                acc_table_lines = [
                    f"### 包{pkg.package_id} 验收要求响应表",
                    "| 序号 | 验收要求 | 响应承诺 | 验收方式 | 证据材料 |",
                    "|---:|---|---|---|---|",
                ]
                for idx, row in enumerate(acceptance_rows, start=1):
                    key = _markdown_cell(row.get("key", ""))
                    val = _markdown_cell(row.get("value", ""))
                    resp = _markdown_cell(row.get("response", "满足"))
                    evidence = _markdown_cell(row.get("evidence", "详见验收方案"))
                    acc_table_lines.append(f"| {idx} | {key}：{val} | {resp} | 按招标文件要求 | {evidence} |")
                service_sections.append("\n".join(acc_table_lines))

            # ── 资料/文档要求分表 ──
            if doc_rows:
                doc_table_lines = [
                    f"### 包{pkg.package_id} 资料/文档要求响应表",
                    "| 序号 | 资料要求 | 响应承诺 | 提供方式 | 证据材料 |",
                    "|---:|---|---|---|---|",
                ]
                for idx, row in enumerate(doc_rows, start=1):
                    key = _markdown_cell(row.get("key", ""))
                    val = _markdown_cell(row.get("value", ""))
                    resp = _markdown_cell(row.get("response", "满足"))
                    evidence = _markdown_cell(row.get("evidence", "详见随机资料"))
                    doc_table_lines.append(f"| {idx} | {key}：{val} | {resp} | 随设备提供 | {evidence} |")
                service_sections.append("\n".join(doc_table_lines))
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

    # 合并服务/配置分表
    service_block = ""
    if service_sections:
        service_block = "\n\n## 四、售后服务/配置/验收/资料要求响应\n\n" + "\n\n".join(service_sections)

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
说明：本章技术偏离表仅含技术类条款，售后服务/配置要求已分表处理（见下方）。
{service_block}
"""
    return BidDocumentSection(section_title="第三章 商务及技术部分", content=content.strip())


def _gen_appendix(
    llm: ChatOpenAI,
    tender: TenderDocument,
    tender_raw: str,
    products: dict | None = None,
    *,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, dict[str, Any]] | None = None,
) -> BidDocumentSection:
    """第四章：报价书附件（技术参数明细 + 售后服务方案）"""
    _ = llm
    products = products or {}
    today = _today()
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    payment = _normalize_commitment_term(tender.commercial_terms.payment_method)

    parameter_tables: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            parameter_tables.append(
                _build_main_parameter_table(
                    pkg,
                    tender_raw,
                    product=products.get(pkg.package_id),
                    normalized_result=normalized_result,
                    evidence_result=evidence_result,
                    product_profile=(product_profiles or {}).get(pkg.package_id),
                )
            )
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
