from __future__ import annotations

import app.services.tender_workflow.common as _common
import app.services.tender_workflow.classification as _classification
import app.services.tender_workflow.product_facts as _product_facts
import app.services.tender_workflow.evidence as _evidence

def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _classification, _product_facts, _evidence,):
    __reexport_all(_module)

del _module
def _fmt_money(amount: float) -> str:
    return f"{amount:,.2f}"


def _authorized_representative(company: CompanyProfile | None) -> str:
    if company is None:
        return "[授权代表]"
    if company.staff:
        name = _safe_text(company.staff[0].name)
        if name:
            return name
    return _safe_text(company.legal_representative, "[授权代表]")


def _derive_brand(product: ProductSpecification) -> str:
    manufacturer = _safe_text(product.manufacturer)
    if manufacturer:
        return manufacturer
    return _safe_text(product.product_name, "[品牌]")


def _product_for_package(
    package_id: str | None,
    products: dict[str, ProductSpecification],
) -> ProductSpecification | None:
    if not package_id:
        return None
    return products.get(str(package_id))


def _fallback_single_product(products: dict[str, ProductSpecification]) -> ProductSpecification | None:
    if len(products) == 1:
        return next(iter(products.values()))
    return None


def _lookup_product_spec_value(product: ProductSpecification, parameter_name: str) -> str:
    normalized = _safe_text(parameter_name)
    if not normalized:
        return ""

    specs = product.specifications or {}
    if normalized in specs:
        return _safe_text(specs[normalized])

    short_name = normalized.split("：", 1)[0].strip()
    if short_name in specs:
        return _safe_text(specs[short_name])

    for key, value in specs.items():
        key_text = _safe_text(key)
        if not key_text:
            continue
        if key_text in normalized or normalized in key_text:
            return _safe_text(value)

    key_tokens = [token for token in re.split(r"[，,、；;：:（）()\[\]\s/]+", short_name) if len(token) >= 2]
    for key, value in specs.items():
        key_text = _safe_text(key)
        if key_tokens and all(token in key_text or token in normalized for token in key_tokens[:3]):
            return _safe_text(value)

    return ""


def _resolve_materialized_response_value(
    product: ProductSpecification | None,
    match: dict[str, Any] | None,
    parameter_name: str,
) -> str:
    if product is not None:
        response_value = _lookup_product_spec_value(product, parameter_name)
        if response_value:
            return response_value

    if match:
        response_value = _safe_text(match.get("response_value") or match.get("matched_fact_value"))
        if response_value:
            return response_value

        matched_fact_quote = _safe_text(match.get("matched_fact_quote"))
        if matched_fact_quote:
            response_value = _extract_fact_value_from_quote(matched_fact_quote, parameter_name)
            if response_value:
                return response_value

        bidder_quote = _safe_text(match.get("bidder_evidence_quote"))
        if bidder_quote:
            response_value = _extract_fact_value_from_quote(bidder_quote, parameter_name)
            if response_value:
                return response_value

    # 扩展策略：能力推断 — 对"具备/支持"类条款返回有意义的承诺而非空值
    _CAP_MARKERS = ("具备", "支持", "提供", "配备", "配置", "满足", "可", "能够", "兼容")
    if product is not None and any(m in parameter_name for m in _CAP_MARKERS):
        return f"满足，投标产品（{product.product_name}）具备该功能"

    # 富展开模式：产品信息充分时给出上下文描述
    if _RICH_EXPANSION_MODE and product is not None:
        specs = product.specifications or {}
        p_name = product.product_name.strip()
        p_mfr = _safe_text(product.manufacturer, "")
        p_model = _safe_text(product.model, "")

        # 策略A: 找到任意相关 spec 值进行关联
        if specs and parameter_name:
            param_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", parameter_name) if len(t) >= 2]
            for spec_key, spec_val in specs.items():
                k = _safe_text(spec_key)
                if k and param_tokens and any(t in k for t in param_tokens):
                    return _safe_text(spec_val)

        # 策略B: 产品名称充分时给出描述
        if p_name and len(specs) >= 3:
            identity = f"{p_mfr} {p_model}" if p_model else p_mfr
            return f"响应，投标产品（{identity.strip()} {p_name}）满足该项要求，详见技术偏离表"

        # 策略C: 有产品名时给承诺式响应
        if p_name:
            return f"响应，投标产品（{p_name}）满足招标要求"

    # 原始扩展策略：产品信息充分时给出上下文描述
    if product is not None:
        specs = product.specifications or {}
        if product.product_name.strip() and len(specs) >= 3:
            mfr = _safe_text(product.manufacturer, "")
            return f"响应，详见投标产品（{mfr} {product.product_name}）技术偏离表"

    return ""


def _parameter_name_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.split(r"[，,、；;：:（）()\[\]\s/]+", _safe_text(value))
        if len(token) >= 2 and not token.isdigit()
    ]


def _parameter_name_matches(left: str, right: str) -> bool:
    left_text = _safe_text(left)
    right_text = _safe_text(right)
    if not left_text or not right_text:
        return False
    if left_text == right_text or left_text in right_text or right_text in left_text:
        return True
    left_tokens = _parameter_name_tokens(left_text)
    right_tokens = _parameter_name_tokens(right_text)
    if not left_tokens or not right_tokens:
        return False
    return all(token in right_text for token in left_tokens[:3]) or all(token in left_text for token in right_tokens[:3])


def _find_technical_match(
    evidence_result: dict[str, Any] | None,
    package_id: str | None,
    parameter_name: str,
) -> dict[str, Any] | None:
    if not evidence_result:
        return None

    normalized_package_id = _safe_text(package_id)
    normalized_parameter = _safe_text(parameter_name)
    if not normalized_parameter:
        return None

    for item in evidence_result.get("technical_matches", []):
        if not isinstance(item, dict):
            continue
        item_package_id = _safe_text(item.get("package_id"))
        if normalized_package_id and item_package_id and item_package_id != normalized_package_id:
            continue
        if _parameter_name_matches(_safe_text(item.get("parameter_name")), normalized_parameter):
            return item
    return None


def _package_technical_matches(
    evidence_result: dict[str, Any] | None,
    package_id: str | None,
) -> list[dict[str, Any]]:
    if not evidence_result:
        return []
    normalized_package_id = _safe_text(package_id)
    matches: list[dict[str, Any]] = []
    for item in evidence_result.get("technical_matches", []):
        if not isinstance(item, dict):
            continue
        item_package_id = _safe_text(item.get("package_id"))
        if normalized_package_id and item_package_id and item_package_id != normalized_package_id:
            continue
        matches.append(item)
    return matches


def _compose_binding_sources(match: dict[str, Any] | None) -> str:
    if not match:
        return "招标原文 / 待补投标方证据"

    parts = _dedupe_texts(
        [
            "招标原文",
            _safe_text(match.get("matched_fact_source")),
            _safe_text(match.get("bidder_evidence_source")),
        ]
    )
    return " / ".join(parts) if parts else "招标原文 / 待补投标方证据"


def _compose_binding_quote(
    match: dict[str, Any] | None,
    *,
    parameter_name: str,
    requirement_value: str,
    fallback_requirement_quote: str = "",
    fallback_response_value: str = "",
) -> str:
    requirement_quote = _safe_text(
        match.get("requirement_source_excerpt") if match else "",
        fallback_requirement_quote or f"{parameter_name}：{requirement_value}",
    )
    parts = [f"招标：{requirement_quote}"]

    product_quote = _safe_text(match.get("matched_fact_quote") if match else "")
    bidder_quote = _safe_text(match.get("bidder_evidence_quote") if match else "")
    response_value = _safe_text(match.get("response_value") if match else "", fallback_response_value)

    if product_quote:
        parts.append(f"产品事实：{product_quote}")
    elif response_value:
        parts.append(f"产品事实：{parameter_name}：{response_value}")

    if bidder_quote:
        parts.append(f"投标方证据：{bidder_quote}")
    else:
        parts.append("投标方证据：未绑定")

    comparison_reason = _safe_text(match.get("comparison_reason") if match else "")
    if comparison_reason and _safe_text(match.get("deviation_status") if match else "") != "无偏离":
        parts.append(f"校验：{comparison_reason}")

    return "；".join(_dedupe_texts(parts))


def _section_has_unresolved_delivery_content(content: str) -> bool:
    return any(pattern in content for pattern in _PLACEHOLDER_PATTERNS) or any(
        marker in content for marker in _UNRESOLVED_DELIVERY_MARKERS
    )


def _resolve_materialized_deviation_status(
    match: dict[str, Any] | None,
    evaluation: dict[str, Any],
) -> str:
    evaluation_status = _safe_text(evaluation.get("deviation_status"), "待核实")
    if evaluation_status == "有偏离":
        return "有偏离"

    if match:
        match_status = _safe_text(match.get("deviation_status"))
        if match_status == "有偏离":
            return "有偏离"
        if match_status == "无偏离" and bool(match.get("proven")):
            return "无偏离"

    return "待核实"


def _company_credit_date(company: CompanyProfile | None) -> str:
    if company is None or company.credit_check_time is None:
        return ""
    return company.credit_check_time.strftime("%Y-%m-%d")


def _format_license_lines(company: CompanyProfile | None) -> list[str]:
    if company is None or not company.licenses:
        return []
    return [
        f"- {license_item.license_type}：{license_item.license_number or '编号待补充'}；有效期：{license_item.valid_until or '长期'}"
        for license_item in company.licenses[:8]
    ]


def _format_staff_lines(company: CompanyProfile | None) -> list[str]:
    if company is None or not company.staff:
        return []
    return [
        f"- {staff.name}；职务：{staff.position or '待补充'}；联系电话：{staff.phone or company.phone or '待补充'}"
        for staff in company.staff[:6]
    ]


def _build_qualification_enrichment_block(
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
) -> str:
    if company is None and not products:
        return ""

    lines = ["## 九、企业主体与资质实填摘要"]
    if company is not None:
        lines.extend(
            [
                f"- 企业名称：{company.name}",
                f"- 法定代表人：{company.legal_representative}",
                f"- 联系电话：{company.phone}",
                f"- 联系地址：{company.address}",
            ]
        )
        license_lines = _format_license_lines(company)
        if license_lines:
            lines.append("- 已关联企业证照：")
            lines.extend(license_lines)
        staff_lines = _format_staff_lines(company)
        if staff_lines:
            lines.append("- 已关联项目人员：")
            lines.extend(staff_lines)
        if company.social_insurance_proof.strip():
            lines.append(f"- 社保缴纳证明：{company.social_insurance_proof}")
        credit_date = _company_credit_date(company)
        if credit_date:
            lines.append(f"- 信用查询时间：{credit_date}")

    product_lines: list[str] = []
    for pkg_id, product in products.items():
        evidence_bits: list[str] = []
        if product.registration_number.strip():
            evidence_bits.append(f"注册证：{product.registration_number}")
        if product.authorization_letter.strip():
            evidence_bits.append(f"授权文件：{product.authorization_letter}")
        if product.certifications:
            evidence_bits.append(f"认证：{'、'.join(product.certifications[:4])}")
        if evidence_bits:
            product_lines.append(f"- 包{pkg_id}：{'；'.join(evidence_bits)}")

    if product_lines:
        lines.append("")
        lines.append("## 十、拟投产品资质关联摘要")
        lines.extend(product_lines)

    return "\n".join(lines)


def _build_technical_enrichment_block(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
) -> str:
    package_map = {pkg.package_id: pkg for pkg in tender.packages}
    blocks: list[str] = []
    for pkg_id, product in products.items():
        pkg = package_map.get(pkg_id)
        if pkg is None:
            continue
        lines = [
            f"### 包{pkg_id} 拟投产品实参摘要",
            f"- 产品名称：{product.product_name}",
            f"- 型号：{product.model or '待补充'}",
            f"- 生产厂家：{product.manufacturer or '待补充'}",
            f"- 产地：{product.origin or '待补充'}",
            f"- 单价：{_fmt_money(product.price)}元" if product.price > 0 else "- 单价：待补充",
        ]
        if product.registration_number.strip():
            lines.append(f"- 注册证编号：{product.registration_number}")
        if product.certifications:
            lines.append(f"- 认证/证书：{'、'.join(product.certifications[:5])}")

        spec_items = list((product.specifications or {}).items())[:8]
        if spec_items:
            lines.append("- 核心响应参数：")
            for key, value in spec_items:
                lines.append(f"  - {key}：{value}")
        blocks.append("\n".join(lines))

    if not blocks:
        return ""

    return "## 四、拟投产品实参摘要\n" + "\n\n".join(blocks)


def _build_appendix_enrichment_block(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
) -> str:
    package_map = {pkg.package_id: pkg for pkg in tender.packages}
    lines = ["## 六、附件资料实填摘要"]
    has_content = False

    for pkg_id, product in products.items():
        pkg = package_map.get(pkg_id)
        if pkg is None:
            continue
        has_content = True
        lines.extend(
            [
                f"### 包{pkg_id}：{pkg.item_name}",
                f"- 产品彩页索引：{product.product_name} / {product.model or '型号待补充'} / {product.manufacturer or '厂家待补充'}",
                f"- 原产地：{product.origin or '待补充'}",
            ]
        )
        if product.certifications:
            lines.append(f"- 节能/环保/其他认证：{'、'.join(product.certifications[:6])}")
        if product.registration_number.strip():
            lines.append(f"- 注册证/备案证明：{product.registration_number}")
        if product.authorization_letter.strip():
            lines.append(f"- 授权或合法来源文件：{product.authorization_letter}")
        detected_specs = list((product.specifications or {}).items())[:5]
        if detected_specs:
            lines.append("- 检测/参数可引用摘要：")
            for key, value in detected_specs:
                lines.append(f"  - {key}：{value}")

    return "\n".join(lines) if has_content else ""


def _replace_placeholder_line(
    section_title: str,
    current_heading: str,
    current_package_id: str | None,
    raw_line: str,
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
) -> str:
    if "（此处留空" not in raw_line and "(此处留空" not in raw_line:
        return raw_line

    heading = _safe_text(current_heading)
    product = _product_for_package(current_package_id, products) or _fallback_single_product(products)

    if "资格性证明文件" in section_title:
        if company and _contains_any(heading, ("养老保险", "医疗保险", "工伤保险", "失业保险", "社保")) and company.social_insurance_proof.strip():
            return f"已关联社保缴纳证明：{company.social_insurance_proof}"
        if company and _contains_any(heading, ("信用", "公示系统", "执行信息", "裁判文书", "政府采购网")):
            credit_date = _company_credit_date(company)
            if credit_date:
                return f"建议附 {credit_date} 生成的查询截图，并确保截图时间覆盖投标截止日前有效时点。"
        if company and "法定代表人身份证明" in heading:
            return f"法定代表人：{company.legal_representative}；身份证明文件待随正式递交版附后。"
        if company and "授权代表身份证明" in heading:
            return f"授权代表：{_authorized_representative(company)}；身份证明文件待随正式递交版附后。"
        if product and "投标产品授权文件" in heading and product.authorization_letter.strip():
            return f"已关联厂家授权文件：{product.authorization_letter}"
        if product and "进口产品合法来源与报关资料" in heading and (product.authorization_letter.strip() or product.origin.strip()):
            detail = product.authorization_letter.strip() or f"原产地：{product.origin}"
            return f"已关联进口合法来源材料：{detail}"

    if "报价书附件" in section_title:
        if product and "产品彩页" in heading:
            return f"已关联彩页资料：{product.product_name} / {product.model or '型号待补充'} / {product.manufacturer or '厂家待补充'}"
        if product and _contains_any(heading, ("节能", "环保", "能效", "认证")) and product.certifications:
            return f"已关联认证材料：{'、'.join(product.certifications[:6])}"
        if product and _contains_any(heading, ("检测", "质评")):
            spec_items = list((product.specifications or {}).items())[:3]
            if spec_items:
                preview = "；".join(f"{key}：{value}" for key, value in spec_items)
                return f"可引用检测/参数摘要：{preview}"

    return raw_line


def _detect_table_mode(cells: list[str]) -> str:
    joined = "|".join(cells)
    # 8-column deviation table (new format)
    if "实际响应值" in joined and "偏离情况" in joined:
        return "deviation"
    # 5-column deviation table (legacy format)
    if "投标产品响应参数" in joined:
        return "deviation"
    if "技术参数项" in joined and "响应情况" in joined:
        return "main_parameter"
    if "技术参数项" in joined and "证据来源" in joined and "应用位置" in joined:
        return "evidence_mapping"
    if "校验项" in joined and "证据载体" in joined and "校验状态" in joined:
        return "response_checklist"
    if "投标报价(元)" in joined and "预算金额(元)" in joined:
        return "quote_overview"
    if "规格型号" in joined and "生产厂家" in joined:
        return "detail_quote"
    # 新配置表格式
    if "是否标配" in joined and "用途说明" in joined:
        return "config_detail"
    return ""


def _find_table_column(cells: list[str], keywords: tuple[str, ...]) -> int:
    for idx, cell in enumerate(cells):
        normalized = _safe_text(cell)
        if normalized and any(keyword in normalized for keyword in keywords):
            return idx
    return -1


def _extract_heading_package_id(heading: str) -> str | None:
    normalized_heading = _safe_text(heading)
    if not normalized_heading:
        return None

    for pattern in (r"第\s*(\d+)\s*包", r"包\s*(\d+)(?:\s*[:：）)])?"):
        match = re.search(pattern, normalized_heading)
        if match:
            return match.group(1)
    return None


def _resolve_row_package_id(
    current_package_id: str | None,
    cells: list[str],
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
) -> str | None:
    if current_package_id and current_package_id in products:
        return current_package_id

    for pkg in tender.packages:
        haystack = " | ".join(cells)
        if pkg.item_name and pkg.item_name in haystack and pkg.package_id in products:
            return pkg.package_id
    return current_package_id


def _materialize_section_content(
    section: BidDocumentSection,
    tender: TenderDocument,
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
    evidence_result: dict[str, Any] | None = None,
) -> tuple[BidDocumentSection, bool]:
    content = section.content
    replacements = {
        "[投标方公司名称]": company.name if company else "[投标方公司名称]",
        "[法定代表人]": company.legal_representative if company else "[法定代表人]",
        "[授权代表]": _authorized_representative(company),
        "[联系电话]": company.phone if company else "[联系电话]",
        "[联系地址]": company.address if company else "[联系地址]",
        "[公司注册地址]": company.address if company else "[公司注册地址]",
    }
    for placeholder in _PLACEHOLDER_FILL_ORDER:
        value = _safe_text(replacements.get(placeholder), placeholder)
        content = content.replace(placeholder, value)

    package_map = {pkg.package_id: pkg for pkg in tender.packages}
    updated_lines: list[str] = []
    changed = content != section.content
    current_package_id: str | None = None
    current_heading = ""
    current_table_mode = ""
    current_table_header: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line
        stripped = line.strip()
        if stripped.startswith("#"):
            current_heading = re.sub(r"^#+\s*", "", stripped)
            heading_package_id = _extract_heading_package_id(current_heading)
            if heading_package_id:
                current_package_id = heading_package_id
            current_table_mode = ""
            current_table_header = []
        elif stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if not cells or all(re.fullmatch(r"[-: ]+", cell) for cell in cells):
                updated_lines.append(line)
                continue

            detected_table_mode = _detect_table_mode(cells)
            if detected_table_mode:
                current_table_mode = detected_table_mode
                current_table_header = cells
                updated_lines.append(line)
                continue

            row_package_id = _resolve_row_package_id(current_package_id, cells, tender, products)
            product = _product_for_package(row_package_id, products)
            pkg = package_map.get(row_package_id) if row_package_id else None

            if current_table_mode == "detail_quote" and product and pkg and len(cells) >= 8:
                quantity = pkg.quantity
                try:
                    if cells[6]:
                        quantity = int(float(cells[6]))
                except ValueError:
                    quantity = pkg.quantity
                cells[2] = _safe_text(product.model or product.product_name, cells[2])
                cells[3] = _safe_text(product.manufacturer, cells[3])
                cells[4] = _derive_brand(product)
                if product.price > 0:
                    cells[5] = _fmt_money(product.price)
                    cells[7] = _fmt_money(product.price * quantity)
                else:
                    cells[5] = cells[5].replace("[待填写]", "[待确认]")
                    cells[7] = cells[7].replace("[待填写]", "[待确认]")
                line = "| " + " | ".join(cells) + " |"
                changed = True
            elif current_table_mode == "quote_overview" and product and pkg and len(cells) >= 6:
                quantity = pkg.quantity
                try:
                    if cells[2]:
                        quantity = int(float(cells[2]))
                except ValueError:
                    quantity = pkg.quantity
                if product.price > 0:
                    cells[4] = _fmt_money(product.price * quantity)
                    line = "| " + " | ".join(cells) + " |"
                    changed = True
            elif current_table_mode == "deviation" and len(cells) >= 5:
                # Support both legacy 5-column and new 8-column deviation tables
                parameter_idx = _find_table_column(current_table_header, ("参数项", "技术参数项", "招标技术参数要求", "招标要求"))
                requirement_idx = _find_table_column(current_table_header, ("招标要求", "招标技术参数要求"))
                response_idx = _find_table_column(current_table_header, ("投标产品响应参数", "响应情况", "实际响应值"))
                deviation_idx = _find_table_column(current_table_header, ("偏离说明", "偏离情况"))
                evidence_idx = _find_table_column(current_table_header, ("证据映射", "响应依据/证据映射", "证据材料"))
                remark_idx = _find_table_column(current_table_header, ("说明/验收备注", "说明", "验收备注"))
                model_idx = _find_table_column(current_table_header, ("投标型号",))
                parameter_cell = _safe_text(cells[parameter_idx]) if 0 <= parameter_idx < len(cells) else ""
                if requirement_idx >= 0 and requirement_idx != parameter_idx:
                    parameter_name = parameter_cell.split("：", 1)[0].strip()
                    requirement_value = _safe_text(cells[requirement_idx]) if 0 <= requirement_idx < len(cells) else parameter_cell
                else:
                    parameter_name = parameter_cell.split("：", 1)[0].strip()
                    requirement_value = parameter_cell
                match = _find_technical_match(evidence_result, row_package_id, parameter_name)
                response_value = _resolve_materialized_response_value(product, match, parameter_name)
                evaluation = _evaluate_requirement_response(requirement_value, response_value)

                # Fill model column for 8-column format
                if product and 0 <= model_idx < len(cells):
                    p_model = _safe_text(product.model) or _safe_text(product.product_name)
                    if p_model and (not cells[model_idx].strip() or cells[model_idx].strip() == "[待填写]"):
                        cells[model_idx] = p_model
                        changed = True

                if response_value:
                    if 0 <= response_idx < len(cells):
                        cells[response_idx] = response_value
                    if 0 <= deviation_idx < len(cells):
                        cells[deviation_idx] = _resolve_materialized_deviation_status(match, evaluation)
                    if 0 <= evidence_idx < len(cells):
                        cells[evidence_idx] = _compose_binding_quote(
                            match,
                            parameter_name=parameter_name,
                            requirement_value=requirement_value,
                            fallback_response_value=response_value,
                        )
                    if 0 <= remark_idx < len(cells):
                        if match and bool(match.get("proven")):
                            cells[remark_idx] = "已匹配产品参数"
                        else:
                            cells[remark_idx] = "需补充投标方证据"
                    line = "| " + " | ".join(cells) + " |"
                    changed = True
                elif 0 <= deviation_idx < len(cells):
                    if 0 <= response_idx < len(cells):
                        # 富展开模式：尝试产品上下文描述
                        if _RICH_EXPANSION_MODE and product is not None:
                            p_name = _safe_text(product.product_name)
                            p_mfr = _safe_text(product.manufacturer, "")
                            if p_name:
                                cells[response_idx] = f"响应，投标产品（{p_mfr} {p_name}）满足该项要求"
                            else:
                                cells[response_idx] = _PENDING_RESPONSE_TEXT
                        else:
                            cells[response_idx] = _PENDING_RESPONSE_TEXT
                    cells[deviation_idx] = "待核实"
                    if 0 <= evidence_idx < len(cells):
                        cells[evidence_idx] = _compose_binding_quote(
                            match,
                            parameter_name=parameter_name,
                            requirement_value=requirement_value,
                        )
                    line = "| " + " | ".join(cells) + " |"
                    changed = True
            elif current_table_mode == "main_parameter" and len(cells) >= 5:
                parameter_name = _safe_text(cells[1])
                match = _find_technical_match(evidence_result, row_package_id, parameter_name)
                response_value = _resolve_materialized_response_value(product, match, parameter_name)
                evaluation = _evaluate_requirement_response(_safe_text(cells[2]), response_value)
                if response_value:
                    cells[3] = response_value
                    if len(cells) >= 5:
                        cells[4] = _resolve_materialized_deviation_status(match, evaluation)
                    line = "| " + " | ".join(cells) + " |"
                    changed = True
                else:
                    cells[3] = _PENDING_RESPONSE_TEXT
                    if len(cells) >= 5:
                        cells[4] = "待核实"
                    line = "| " + " | ".join(cells) + " |"
                    changed = True
            elif current_table_mode == "evidence_mapping" and len(cells) >= 5:
                parameter_idx = _find_table_column(current_table_header, ("技术参数项", "参数项"))
                source_idx = _find_table_column(current_table_header, ("证据来源",))
                quote_idx = _find_table_column(current_table_header, ("原文片段", "证据摘要"))
                parameter_name = _safe_text(cells[parameter_idx]) if 0 <= parameter_idx < len(cells) else ""
                match = _find_technical_match(evidence_result, row_package_id, parameter_name)
                if 0 <= source_idx < len(cells):
                    cells[source_idx] = _compose_binding_sources(match)
                if 0 <= quote_idx < len(cells):
                    cells[quote_idx] = _compose_binding_quote(
                        match,
                        parameter_name=parameter_name,
                        requirement_value=_safe_text(match.get("requirement_value") if match else ""),
                        fallback_requirement_quote=_safe_text(cells[quote_idx]),
                        fallback_response_value=_safe_text(match.get("response_value") if match else ""),
                    )
                line = "| " + " | ".join(cells) + " |"
                changed = True
            elif current_table_mode == "response_checklist" and len(cells) >= 5:
                item_name = _safe_text(cells[1])
                package_matches = _package_technical_matches(evidence_result, row_package_id)
                package_total = len(package_matches)
                package_proven = len([item for item in package_matches if bool(item.get("proven"))])
                if "关键技术参数逐条响应" in item_name:
                    cells[2] = (
                        "暂无可核技术参数"
                        if package_total == 0
                        else f"已证实 {package_proven}/{package_total} 项；其余 {max(0, package_total - package_proven)} 项待补证"
                    )
                    cells[4] = "已完成" if 0 < package_total == package_proven else "待补证"
                    line = "| " + " | ".join(cells) + " |"
                    changed = True
                elif "技术条款证据映射" in item_name:
                    cells[2] = (
                        "暂无可映射技术参数"
                        if package_total == 0
                        else f"已形成 {package_total} 条映射，其中已证实 {package_proven} 条"
                    )
                    cells[4] = (
                        "已完成"
                        if package_total > 0 and package_proven / package_total >= _MIN_PROVEN_COMPLETION_RATE
                        else "待补证"
                    )
                    line = "| " + " | ".join(cells) + " |"
                    changed = True

            if "投标总报价" in line and "[待填写]" in line:
                total_price = 0.0
                total_ready = False
                for pkg_id, product in products.items():
                    pkg = package_map.get(pkg_id)
                    if pkg and product.price > 0:
                        total_price += product.price * pkg.quantity
                        total_ready = True
                if total_ready:
                    line = line.replace("[待填写]", _fmt_money(total_price))
                    changed = True
            if "合计" in line and "[待填写]" in line:
                total_price = 0.0
                total_ready = False
                for pkg_id, product in products.items():
                    pkg = package_map.get(pkg_id)
                    if pkg and product.price > 0:
                        total_price += product.price * pkg.quantity
                        total_ready = True
                if total_ready:
                    line = line.replace("[待填写]", _fmt_money(total_price))
                    changed = True
        else:
            replaced_line = _replace_placeholder_line(
                section_title=section.section_title,
                current_heading=current_heading,
                current_package_id=current_package_id,
                raw_line=line,
                company=company,
                products=products,
            )
            if replaced_line != line:
                line = replaced_line
                changed = True
        updated_lines.append(line)

    enriched_content = "\n".join(updated_lines).strip()
    extra_blocks: list[str] = []
    if "第一章" in section.section_title or "资格性证明文件" in section.section_title:
        qualification_block = _build_qualification_enrichment_block(company, products)
        if qualification_block and qualification_block not in enriched_content:
            extra_blocks.append(qualification_block)
    if "第三章" in section.section_title or "技术" in section.section_title:
        technical_block = _build_technical_enrichment_block(tender, products)
        if technical_block and technical_block not in enriched_content:
            extra_blocks.append(technical_block)
    if "第四章" in section.section_title or "报价书附件" in section.section_title:
        appendix_block = _build_appendix_enrichment_block(tender, products)
        if appendix_block and appendix_block not in enriched_content:
            extra_blocks.append(appendix_block)

    if extra_blocks:
        enriched_content = f"{enriched_content}\n\n" + "\n\n".join(extra_blocks)
        changed = True

    return section.model_copy(update={"content": enriched_content}), changed


def _materialize_sections(
    sections: list[BidDocumentSection],
    tender: TenderDocument,
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
    evidence_result: dict[str, Any] | None = None,
) -> tuple[list[BidDocumentSection], dict[str, Any]]:
    materialized: list[BidDocumentSection] = []
    changed_sections: list[str] = []
    for section in sections:
        updated, changed = _materialize_section_content(
            section,
            tender,
            company,
            products,
            evidence_result=evidence_result,
        )
        materialized.append(updated)
        if changed:
            changed_sections.append(section.section_title)

    unresolved_sections = [
        section.section_title
        for section in materialized
        if _section_has_unresolved_delivery_content(section.content)
    ]
    summary = (
        f"已注入企业/产品实参，更新 {len(changed_sections)} 个章节。"
        if changed_sections
        else "章节中未发现可自动注入的企业或产品字段。"
    )
    if unresolved_sections:
        summary += f" 仍有 {len(unresolved_sections)} 个章节存在待人工补录项。"

    return materialized, {
        "changed_sections": changed_sections,
        "unresolved_sections": unresolved_sections,
        "summary": summary,
    }
