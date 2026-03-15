from __future__ import annotations

import re
from typing import Any

from app.schemas import (
    BidDocumentSection,
    CompanyProfile,
    ProcurementPackage,
    ProductSpecification,
    TenderDocument,
)
from app.services.tender_workflow.common import (
    _MIN_PROVEN_COMPLETION_RATE,
    _PLACEHOLDER_FILL_ORDER,
    _PLACEHOLDER_PATTERNS,
    _UNRESOLVED_DELIVERY_MARKERS,
    _contains_any,
    _dedupe_texts,
    _evaluate_requirement_response,
    _extract_fact_value_from_quote,
    _lookup_package_fact_value,
    _lookup_product_spec_value,
    _parameter_name_matches,
    _safe_text,
)
from app.services.tender_workflow.product_facts import _build_product_profile

import app.services.tender_workflow.common as _common
import app.services.tender_workflow.classification as _classification
import app.services.tender_workflow.product_facts as _product_facts
import app.services.tender_workflow.evidence as _evidence


def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _classification, _product_facts, _evidence,):
    __reexport_all(_module)

del _module
def _fmt_money(amount: float) -> str:
    """格式化金额显示。"""
    return f"{amount:,.2f}"


def _authorized_representative(company: CompanyProfile | None) -> str:
    """解析可用于落款的授权代表姓名。"""
    if company is None:
        return "[授权代表]"
    if company.staff:
        name = _safe_text(company.staff[0].name)
        if name:
            return name
    return _safe_text(company.legal_representative, "[授权代表]")


def _derive_brand(product: ProductSpecification) -> str:
    """推断产品品牌展示文本。"""
    manufacturer = _safe_text(product.manufacturer)
    if manufacturer:
        return manufacturer
    return _safe_text(product.product_name, "[品牌]")


def _product_for_package(
    package_id: str | None,
    products: dict[str, ProductSpecification],
) -> ProductSpecification | None:
    """按包号获取对应产品。"""
    if not package_id:
        return None
    return products.get(str(package_id))


def _fallback_single_product(products: dict[str, ProductSpecification]) -> ProductSpecification | None:
    """在仅有一个产品时返回兜底产品。"""
    if len(products) == 1:
        return next(iter(products.values()))
    return None



def _resolve_materialized_response_value(
    product: ProductSpecification | None,
    match: dict[str, Any] | None,
    parameter_name: str,
    package_facts: dict[str, Any] | None = None,
) -> str:
    """解析并返回实装响应值。"""
    if product is not None:
        response_value = _lookup_product_spec_value(product, parameter_name)
        if response_value:
            return response_value

    if match:
        for key in ("response_value", "matched_fact_value"):
            value = _safe_text(match.get(key), "")
            if value and value.lower() not in {"none", "null", "nan"}:
                return value

    if package_facts:
        fact_value, _, _ = _lookup_package_fact_value(package_facts, parameter_name)
        if fact_value:
            return _safe_text(fact_value)

    if match:
        for quote_key in ("matched_fact_quote", "bidder_evidence_quote"):
            quote = _safe_text(match.get(quote_key), "")
            if not quote:
                continue
            value = _extract_fact_value_from_quote(quote, parameter_name)
            if value:
                return value

    return ""




def _package_technical_matches(
    evidence_result: dict[str, Any] | None,
    package_id: str | None,
) -> list[dict[str, Any]]:
    """筛选指定包件的技术匹配结果。"""
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
    """组合bindingsources。"""
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
    """组合binding报价。"""
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
    """判断是否存在章节unresolved交付内容。"""
    return any(pattern in content for pattern in _PLACEHOLDER_PATTERNS) or any(
        marker in content for marker in _UNRESOLVED_DELIVERY_MARKERS
    )


def _resolve_materialized_deviation_status(
    match: dict[str, Any] | None,
    evaluation: dict[str, Any],
) -> str:
    """解析并返回materializeddeviation状态。"""
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
    """返回credit日期。"""
    if company is None or company.credit_check_time is None:
        return ""
    return company.credit_check_time.strftime("%Y-%m-%d")


def _format_license_lines(company: CompanyProfile | None) -> list[str]:
    """格式化资质行。"""
    if company is None or not company.licenses:
        return []
    return [
        f"- {license_item.license_type}：{license_item.license_number or '编号待补充'}；有效期：{license_item.valid_until or '长期'}"
        for license_item in company.licenses[:8]
    ]


def _format_staff_lines(company: CompanyProfile | None) -> list[str]:
    """格式化人员信息行。"""
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
    """构建资格审查enrichment文本块。"""
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
    """构建技术enrichment文本块。"""
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
    """构建appendixenrichment文本块。"""
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


def _document_date_text(
    company: CompanyProfile | None,
    *,
    placeholder: str = "【待填写：日期】",
) -> str:
    """返回日期文本。"""
    if company is None:
        return placeholder

    text = _safe_text(getattr(company, "document_date", ""), "")
    if not text or "待填写" in text or "待补充" in text:
        return placeholder
    return text


def _staff_position(company: CompanyProfile | None) -> str:
    """推断授权人员的岗位名称。"""
    if company is None or not company.staff:
        return ""
    return _safe_text(company.staff[0].position, "")


def _product_identity_placeholder_text(product: ProductSpecification | None) -> str:
    """返回identity占位符文本。"""
    if product is None:
        return "【待填写：品牌/型号，产地】"
    model = _safe_text(product.model or product.product_name, "")
    origin = _safe_text(product.origin, "")
    if model and origin:
        return f"{model}，{origin}"
    return model or origin or "【待填写：品牌/型号，产地】"


def _apply_structured_placeholders(
    content: str,
    company: CompanyProfile | None,
    product: ProductSpecification | None,
) -> str:
    """按结构化资料替换章节中的占位符。"""
    auth_rep = _authorized_representative(company)
    position = _staff_position(company)
    replacements = {
        "【待填写：投标人名称】": _safe_text(company.name if company else "", "【待填写：投标人名称】"),
        "【待填写：法定代表人姓名】": _safe_text(company.legal_representative if company else "", "【待填写：法定代表人姓名】"),
        "【待填写：法定代表人】": _safe_text(company.legal_representative if company else "", "【待填写：法定代表人】"),
        "【待填写：授权代表】": _safe_text(auth_rep, "【待填写：授权代表】"),
        "【待填写：授权代表姓名】": _safe_text(auth_rep, "【待填写：授权代表姓名】"),
        "【待填写：联系电话】": _safe_text(company.phone if company else "", "【待填写：联系电话】"),
        "【待填写：电话】": _safe_text(company.phone if company else "", "【待填写：电话】"),
        "【待填写：联系地址】": _safe_text(company.address if company else "", "【待填写：联系地址】"),
        "【待填写：详细通讯地址】": _safe_text(company.address if company else "", "【待填写：详细通讯地址】"),
        "【待填写：公司注册地址】": _safe_text(company.address if company else "", "【待填写：公司注册地址】"),
        "【待填写：职务】": position or "【待填写：职务】",
        "【待填写：品牌/型号，产地】": _product_identity_placeholder_text(product),
        "【待填写：品牌/型号/产地】": _product_identity_placeholder_text(product),
        "【待填写：品牌/型号】": _safe_text((product.model or product.product_name) if product else "", "【待填写：品牌/型号】"),
        "【待填写：投标型号】": _safe_text((product.model or product.product_name) if product else "", "【待填写：投标型号】"),
        "【待填写：品牌】": _derive_brand(product) if product else "【待填写：品牌】",
        "【待填写：日期】": _document_date_text(company, placeholder="【待填写：日期】"),
        "【待填写：年 月 日】": _document_date_text(company, placeholder="【待填写：年 月 日】"),
        "【待填写：磋商日期】": _document_date_text(company, placeholder="【待填写：磋商日期】"),
        "【待填写：谈判日期】": _document_date_text(company, placeholder="【待填写：谈判日期】"),
    }
    for placeholder, value in replacements.items():
        if value:
            content = content.replace(placeholder, value)
    return content


def _target_packages_for_materialization(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
) -> list[ProcurementPackage]:
    """定位实装的包件。"""
    selected = [pkg for pkg in tender.packages if not products or pkg.package_id in products]
    return selected or list(tender.packages)


def _coarse_technical_section(content: str) -> bool:
    """返回技术章节。"""
    markers = (
        "详见采购文件技术要求",
        "【待填写：逐条响应参数/配置/证据】",
        "【待填写：逐条响应】",
        "谈判文件的参数和要求",
        "响应文件参数",
        "品牌型号、产地",
    )
    return any(marker in content for marker in markers)


def _product_profile_for_materialization(product: ProductSpecification | None) -> dict[str, Any]:
    """整理实装阶段使用的产品画像字典。"""
    if product is None:
        return {
            "config_items": [],
            "functional_notes": "",
            "acceptance_notes": "",
            "training_notes": "",
        }
    return _build_product_profile(product)


def _fallback_requirement_rows(
    pkg: ProcurementPackage,
    product: ProductSpecification | None,
) -> list[dict[str, Any]]:
    """返回需求行。"""
    rows: list[dict[str, Any]] = []
    for key, value in (pkg.technical_requirements or {}).items():
        rows.append(
            {
                "parameter_name": _safe_text(key),
                "requirement_value": _safe_text(value),
                "response_value": _lookup_product_spec_value(product, _safe_text(key)) if product else "",
                "matched_fact_source": "产品参数库",
                "bidder_evidence_source": "",
                "bid_evidence_file": "",
                "bid_evidence_page": None,
                "comparison_reason": "",
                "deviation_status": "无偏离" if product and _lookup_product_spec_value(product, _safe_text(key)) else "待核实",
                "proven": bool(product and _lookup_product_spec_value(product, _safe_text(key))),
            }
        )
    return rows


def _ordered_package_matches(
    pkg: ProcurementPackage,
    product: ProductSpecification | None,
    evidence_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """按展示顺序整理包件匹配结果。"""
    matches = _package_technical_matches(evidence_result, pkg.package_id)
    if not matches:
        return _fallback_requirement_rows(pkg, product)

    key_order = {
        _safe_text(name): idx
        for idx, name in enumerate((pkg.technical_requirements or {}).keys(), start=1)
        if _safe_text(name)
    }
    return sorted(
        matches,
        key=lambda item: (
            key_order.get(_safe_text(item.get("parameter_name")), 10**6),
            _safe_text(item.get("parameter_name")),
        ),
    )


def _resolve_match_evidence_page(
    product: ProductSpecification | None,
    match: dict[str, Any] | None,
    parameter_name: str,
) -> str:
    """解析并返回匹配证据page。"""
    page = None
    if match:
        page = match.get("bid_evidence_page") or match.get("tender_source_page")
    if page in (None, "", 0) and product is not None:
        for ref in product.evidence_refs or []:
            if not isinstance(ref, dict):
                continue
            description = _safe_text(ref.get("description", ""))
            if parameter_name and parameter_name in description:
                page = ref.get("page")
                break
    return str(page) if page not in (None, "", 0) else "待补页码"


def _resolve_match_evidence_label(
    match: dict[str, Any] | None,
) -> str:
    """解析并返回匹配证据标签。"""
    if not match:
        return "待补充投标方证据"
    for key in ("bid_evidence_file", "bidder_evidence_source", "matched_fact_source", "bid_evidence_type"):
        value = _safe_text(match.get(key), "")
        if value:
            return value
    return "待补充投标方证据"


def _build_materialized_technical_section(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
    evidence_result: dict[str, Any] | None,
) -> str:
    """构建materialized技术章节。"""
    parts: list[str] = []
    for pkg in _target_packages_for_materialization(tender, products):
        product = _product_for_package(pkg.package_id, products) or _fallback_single_product(products)
        profile = _product_profile_for_materialization(product)
        rows = _ordered_package_matches(pkg, product, evidence_result)
        model_text = _safe_text((product.model or product.product_name) if product else "", "待补充")

        parts.extend(
            [
                f"### （一）技术偏离及详细配置明细表（第{pkg.package_id}包）",
                f"项目名称：{tender.project_name}",
                f"项目编号：{tender.project_number}",
                f"包件名称：{pkg.item_name}",
                "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |",
                "|---:|---|---|---|---|---|---:|---|",
            ]
        )

        for idx, match in enumerate(rows, start=1):
            parameter_name = _safe_text(match.get("parameter_name"), f"参数{idx}")
            requirement_value = _safe_text(match.get("requirement_value"), _safe_text(match.get("normalized_value"), ""))
            response_value = _resolve_materialized_response_value(product, match, parameter_name)
            evaluation = _evaluate_requirement_response(requirement_value, response_value)
            is_proven = bool(match.get("proven"))
            deviation = (
                _resolve_materialized_deviation_status(match, evaluation)
                if response_value and is_proven
                else "【待填写：无偏离/正偏离/负偏离】"
            )
            evidence_label = _resolve_match_evidence_label(match)
            evidence_page = _resolve_match_evidence_page(product, match, parameter_name)
            note_parts = []
            comparison_reason = _safe_text(match.get("comparison_reason"), "")
            if bool(match.get("proven")):
                note_parts.append("已完成参数与投标证据闭环")
            elif response_value:
                note_parts.append("已写入响应值，待补投标方证据页码")
            else:
                note_parts.append("待补实际响应值与证据")
            if comparison_reason:
                note_parts.append(comparison_reason)
            parts.append(
                "| {seq} | {param} | {req} | {model} | {resp} | {dev} | {evidence} | {page} | {note} |".format(
                    seq=idx,
                    param=parameter_name,
                    req=requirement_value or "详见采购文件技术要求",
                    model=model_text if idx == 1 else "同上",
                    resp=response_value or "【待填写：实际响应值】",
                    dev=deviation,
                    evidence=evidence_label,
                    page=evidence_page,
                    note="；".join(_dedupe_texts(note_parts)),
                )
            )

        config_rows = []
        for idx, item in enumerate(profile.get("config_items", [])[:8], start=1):
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("配置项") or item.get("name"), "")
            qty = _safe_text(item.get("数量") or item.get("qty") or item.get("quantity"), "1")
            desc = _safe_text(item.get("说明") or item.get("remark"), "")
            source = _safe_text(item.get("来源") or item.get("source"), "产品资料")
            if name:
                config_rows.append((idx, name, qty or "1", desc or "标配", source))

        if config_rows:
            parts.extend(
                [
                    "",
                    f"### （二）配置明细表（第{pkg.package_id}包）",
                    "| 序号 | 配置项 | 数量 | 说明 | 来源 |",
                    "|---:|---|---|---|---|",
                ]
            )
            for seq, name, qty, desc, source in config_rows:
                parts.append(f"| {seq} | {name} | {qty} | {desc} | {source} |")

        feature_lines = []
        functional_notes = _safe_text(profile.get("functional_notes"), "")
        if functional_notes:
            feature_lines.append(f"- 功能概述：{functional_notes}")
        for seq, name, _, desc, _ in config_rows[:5]:
            _ = seq
            feature_lines.append(f"- {name}：{desc}")
        if feature_lines:
            parts.extend(["", f"### （二-B）配置功能描述（第{pkg.package_id}包）", *feature_lines])

        acceptance_notes = _safe_text(profile.get("acceptance_notes"), "")
        if acceptance_notes:
            parts.extend(["", f"### （八）验收要点（第{pkg.package_id}包）", f"- {acceptance_notes}"])

        parts.append("")
    return "\n".join(parts).strip()


def _build_materialized_service_section(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
) -> str:
    """构建materialized服务章节。"""
    parts: list[str] = []
    for pkg in _target_packages_for_materialization(tender, products):
        product = _product_for_package(pkg.package_id, products) or _fallback_single_product(products)
        profile = _product_profile_for_materialization(product)
        spec_items = list((product.specifications or {}).items())[:3] if product else []
        spec_digest = "；".join(f"{key}：{value}" for key, value in spec_items) or "按采购文件要求配置"
        product_identity = (
            f"{_safe_text(product.manufacturer, '')} {_safe_text(product.product_name, '')}（型号：{_safe_text(product.model, '待补充')}）"
            if product is not None
            else pkg.item_name
        ).strip()
        acceptance_notes = _safe_text(profile.get("acceptance_notes"), "按采购文件及国家相关标准进行验收。")
        training_notes = _safe_text(profile.get("training_notes"), "提供设备操作培训，确保用户熟练掌握。")
        functional_notes = _safe_text(profile.get("functional_notes"), f"围绕{pkg.item_name}关键功能制定安装调试和交付方案。")
        service_bits = []
        if product and product.registration_number.strip():
            service_bits.append(f"注册证：{product.registration_number}")
        if product and product.authorization_letter.strip():
            service_bits.append(f"授权文件：{product.authorization_letter}")
        if product and product.certifications:
            service_bits.append(f"认证：{'、'.join(product.certifications[:3])}")
        support_digest = "；".join(service_bits) if service_bits else "配合提交说明书、装箱单、合格证等随货资料"

        parts.extend(
            [
                f"### 包{pkg.package_id}：{pkg.item_name}",
                f"- 拟投产品：{product_identity}",
                f"- 交货期：{pkg.delivery_time or '按采购文件约定'}",
                f"- 交货地点：{pkg.delivery_place or '采购人指定地点'}",
                "",
                "#### 1. 供货组织与进度安排",
                f"围绕{pkg.item_name}建立专项交付计划，按“备货复核-发运预约-到货签收-安装调试-培训验收”五个节点推进，本包拟投产品为{product_identity}，重点跟踪{spec_digest}等关键交付信息。",
                "",
                "#### 2. 包装运输与到货保护",
                f"结合{pkg.item_name}的运输特性执行原厂包装、防震防潮和到货外观检查；到货后按装箱清单、随机附件和关键部件逐项点验，异常情况第一时间留痕并启动补发或整改。",
                "",
                "#### 3. 安装调试与场地联动",
                functional_notes,
                "",
                "#### 4. 培训实施",
                training_notes,
                "",
                "#### 5. 验收与资料移交",
                f"{acceptance_notes}；同步移交{support_digest}。",
                "",
                "#### 6. 售后与维保安排",
                f"针对{pkg.item_name}安排项目联系人、安装调试工程师和售后支持人员，围绕{spec_digest}建立巡检、故障响应、备件支持和版本升级的服务闭环。",
                "",
            ]
        )
    parts.extend(["供应商全称：【待填写：投标人名称】", "日期：【待填写：日期】"])
    return "\n".join(parts).strip()


def _maybe_rebuild_section_content(
    section: BidDocumentSection,
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
    evidence_result: dict[str, Any] | None = None,
) -> str:
    """返回rebuild章节内容。"""
    title = _safe_text(section.section_title)
    content = _safe_text(section.content)
    if "技术偏离及详细配置明细表" in title or "技术偏离及详细配置明细表" in content:
        if products and _coarse_technical_section(content):
            return _build_materialized_technical_section(tender, products, evidence_result)
    if "技术服务和售后服务的内容及措施" in title and products:
        return _build_materialized_service_section(tender, products)
    return ""


def _find_review_match(
    evidence_result: dict[str, Any] | None,
    package_id: str | None,
    item_name: str,
    requirement: str = "",
) -> dict[str, Any] | None:
    """查找评审匹配。"""
    haystack = _safe_text(f"{item_name} {requirement}")
    if not haystack:
        return None
    for match in _package_technical_matches(evidence_result, package_id):
        if not isinstance(match, dict):
            continue
        parameter_name = _safe_text(match.get("parameter_name"), "")
        if parameter_name and (parameter_name in haystack or _parameter_name_matches(parameter_name, haystack)):
            return match
    return None


def _resolve_review_location(item_name: str, requirement: str, package_id: str | None) -> str:
    """解析并返回评审location。"""
    haystack = _safe_text(f"{item_name} {requirement}")
    if any(token in haystack for token in ("营业执照", "资格承诺", "授权书", "法定代表人", "授权代表", "信用", "社保")):
        return "第一章 资格性证明文件"
    if any(token in haystack for token in ("报价", "价格", "总价", "投标报价")):
        return "第二章/第三章 报价相关章节"
    if any(token in haystack for token in ("技术", "参数", "品牌", "型号", "配置", "性能", "偏离")):
        return f"技术偏离及详细配置明细表（第{package_id or '对应'}包）"
    if any(token in haystack for token in ("供货", "运输", "安装", "调试", "培训", "验收", "售后", "维保", "升级")):
        return f"技术服务和售后服务的内容及措施（第{package_id or '对应'}包）"
    return "对应章节见正文"


def _resolve_review_evidence(
    item_name: str,
    requirement: str,
    package_id: str | None,
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
    evidence_result: dict[str, Any] | None,
) -> str:
    """解析并返回评审证据。"""
    location = _resolve_review_location(item_name, requirement, package_id)
    haystack = _safe_text(f"{item_name} {requirement}")
    product = _product_for_package(package_id, products) or _fallback_single_product(products)
    match = _find_review_match(evidence_result, package_id, item_name, requirement)

    if match:
        label = _resolve_match_evidence_label(match)
        page = _resolve_match_evidence_page(product, match, _safe_text(match.get("parameter_name"), item_name))
        page_part = f" 第{page}页" if page != "待补页码" else ""
        return f"{location}；{label}{page_part}"

    if company and any(token in haystack for token in ("营业执照", "许可证", "资质")) and company.licenses:
        license_item = company.licenses[0]
        return f"{location}；{license_item.license_type}（{license_item.license_number}）"

    if company and any(token in haystack for token in ("社保", "保险")) and company.social_insurance_proof.strip():
        return f"{location}；{company.social_insurance_proof}"

    if product and any(token in haystack for token in ("注册证", "备案", "医疗器械")) and product.registration_number.strip():
        return f"{location}；注册证编号：{product.registration_number}"

    if product and "授权" in haystack and product.authorization_letter.strip():
        return f"{location}；{product.authorization_letter}"

    if product and any(token in haystack for token in ("认证", "证书", "节能", "环保")) and product.certifications:
        return f"{location}；{'、'.join(product.certifications[:3])}"

    return f"见{location}"


def _replace_placeholder_line(
    section_title: str,
    current_heading: str,
    current_package_id: str | None,
    raw_line: str,
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
) -> str:
    """返回占位符行。"""
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
    """检测表格模式。"""
    joined = "|".join(cells)
    if all(
        header in joined
        for header in ("技术参数项", "采购文件技术要求", "响应文件响应情况", "偏离情况")
    ):
        return "deviation"
    # 8-column deviation table (new format)
    if "实际响应值" in joined and "偏离情况" in joined:
        return "deviation"
    # 5-column deviation table (legacy format)
    if "投标产品响应参数" in joined:
        return "deviation"
    if "技术参数项" in joined and "响应情况" in joined:
        return "deviation"
    if "技术参数项" in joined and "证据来源" in joined and "应用位置" in joined:
        return "evidence_mapping"
    if "校验项" in joined and "证据载体" in joined and "校验状态" in joined:
        return "response_checklist"
    if ("评审项" in joined or "评审因素" in joined) and ("证明材料/页码" in joined or "证明材料页码" in joined):
        return "review_detailed"
    if "投标文件所在页码" in joined or "投标文件对应页码" in joined or "对应材料/页码" in joined:
        return "review_page"
    if "投标报价(元)" in joined and "预算金额(元)" in joined:
        return "quote_overview"
    if "规格型号" in joined and "生产厂家" in joined:
        return "detail_quote"
    # 新配置表格式
    if "是否标配" in joined and "用途说明" in joined:
        return "config_detail"
    return ""


def _find_table_column(cells: list[str], keywords: tuple[str, ...]) -> int:
    """查找表格column。"""
    for idx, cell in enumerate(cells):
        normalized = _safe_text(cell)
        if normalized and any(keyword in normalized for keyword in keywords):
            return idx
    return -1


def _extract_heading_package_id(heading: str) -> str | None:
    """提取heading包件id。"""
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
    """解析并返回行包件id。"""
    if current_package_id and current_package_id in products:
        return current_package_id

    if len(products) == 1:
        return next(iter(products.keys()))

    for pkg in tender.packages:
        haystack = " | ".join(cells)
        if pkg.item_name and pkg.item_name in haystack and pkg.package_id in products:
            return pkg.package_id
    return current_package_id

def _parameter_match_score(left: str, right: str) -> float:
    """计算参数匹配的优先级分数。"""
    left_text = _safe_text(left, "")
    right_text = _safe_text(right, "")
    if not left_text or not right_text:
        return 0.0

    if left_text == right_text:
        return 1.0

    if left_text in right_text or right_text in left_text:
        return 0.85

    left_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", left_text) if len(t) >= 2]
    right_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", right_text) if len(t) >= 2]
    if not left_tokens or not right_tokens:
        return 0.0

    inter = len(set(left_tokens) & set(right_tokens))
    union = len(set(left_tokens) | set(right_tokens))
    if union == 0:
        return 0.0
    return inter / union


def _find_technical_match(
    evidence_result: dict[str, Any] | None,
    package_id: str | None,
    parameter_name: str,
) -> dict[str, Any] | None:
    """查找技术匹配。"""
    if not evidence_result:
        return None

    normalized_package_id = _safe_text(package_id, "")
    normalized_parameter = _safe_text(parameter_name, "")
    if not normalized_parameter:
        return None

    best_item = None
    best_score = 0.0

    for item in evidence_result.get("technical_matches", []):
        if not isinstance(item, dict):
            continue

        item_package_id = _safe_text(item.get("package_id"), "")
        if normalized_package_id and item_package_id and item_package_id != normalized_package_id:
            continue

        score = _parameter_match_score(
            _safe_text(item.get("parameter_name"), ""),
            normalized_parameter,
        )

        if score <= 0:
            continue

        if item.get("matched_fact_value"):
            score += 0.10
        if item.get("response_value"):
            score += 0.05
        if item.get("proven"):
            score += 0.15

        if score > best_score:
            best_score = score
            best_item = item

    return best_item if best_score >= 0.55 else None

def _materialize_section_content(
    section: BidDocumentSection,
    tender: TenderDocument,
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
    evidence_result: dict[str, Any] | None = None,
) -> tuple[BidDocumentSection, bool]:
    """实装章节内容。"""
    content = section.content
    fallback_product = _fallback_single_product(products)
    replacements = {
        "[投标方公司名称]": company.name if company else "[投标方公司名称]",
        "[法定代表人]": company.legal_representative if company else "[法定代表人]",
        "[授权代表]": _authorized_representative(company),
        "[联系电话]": company.phone if company else "[联系电话]",
        "[联系地址]": company.address if company else "[联系地址]",
        "[公司注册地址]": company.address if company else "[公司注册地址]",
        "[品牌型号]": (
            _safe_text(fallback_product.model or fallback_product.product_name, "[品牌型号]")
            if fallback_product else "[品牌型号]"
        ),
        "[生产厂家]": _safe_text(fallback_product.manufacturer, "[生产厂家]") if fallback_product else "[生产厂家]",
        "[品牌]": _derive_brand(fallback_product) if fallback_product else "[品牌]",
    }
    for placeholder in _PLACEHOLDER_FILL_ORDER:
        value = _safe_text(replacements.get(placeholder), placeholder)
        content = content.replace(placeholder, value)
    for placeholder in ("[品牌型号]", "[生产厂家]", "[品牌]"):
        value = _safe_text(replacements.get(placeholder), placeholder)
        content = content.replace(placeholder, value)
    content = content.replace(
        "1. 与投标人单位负责人为同一人的其他单位：[待填写]",
        "1. 与投标人单位负责人为同一人的其他单位：无",
    )
    content = content.replace(
        "2. 与投标人存在直接控股、管理关系的其他单位：[待填写]",
        "2. 与投标人存在直接控股、管理关系的其他单位：无",
    )
    content = _apply_structured_placeholders(content, company, fallback_product)

    rebuilt_content = _maybe_rebuild_section_content(
        section,
        tender,
        products,
        evidence_result=evidence_result,
    )
    was_rebuilt = bool(rebuilt_content)
    if rebuilt_content:
        content = _apply_structured_placeholders(rebuilt_content, company, fallback_product)

    package_map = {pkg.package_id: pkg for pkg in tender.packages}
    updated_lines: list[str] = []
    changed = content != section.content
    current_package_id: str | None = None
    current_heading = ""
    current_table_mode = ""
    current_table_header: list[str] = []
    if was_rebuilt:
        updated_lines = content.splitlines()
    else:
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
                    requirement_idx = _find_table_column(current_table_header, ("采购文件技术要求", "采购文件要求", "招标要求", "招标技术参数要求"))
                    response_idx = _find_table_column(current_table_header, ("响应文件响应情况", "投标产品响应参数", "响应情况", "实际响应值"))
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

                    is_proven = bool(match and match.get("proven"))
                    has_fact = bool(response_value)

                    # 响应列
                    if 0 <= response_idx < len(cells):
                        if has_fact:
                            cells[response_idx] = response_value
                        else:
                            cells[response_idx] = "【待填写：实际响应值】"

                    # 偏离列
                    if 0 <= deviation_idx < len(cells):
                        if has_fact and is_proven:
                            cells[deviation_idx] = _resolve_materialized_deviation_status(match, evaluation)
                        else:
                            cells[deviation_idx] = "【待填写：无偏离/正偏离/负偏离】"

                    # 证据列
                    if 0 <= evidence_idx < len(cells):
                        if match:
                            cells[evidence_idx] = _compose_binding_quote(
                                match,
                                parameter_name=parameter_name,
                                requirement_value=requirement_value,
                                fallback_response_value=response_value,
                            )
                        else:
                            cells[evidence_idx] = "【待补证：说明书/彩页/厂家参数表】"

                    # 备注列
                    if 0 <= remark_idx < len(cells):
                        if is_proven and has_fact:
                            cells[remark_idx] = "已完成参数与投标证据闭环"
                        elif has_fact:
                            cells[remark_idx] = "已写入实参，待补投标方证据页码"
                        else:
                            cells[remark_idx] = "待补实际响应值与证据"

                    line = "| " + " | ".join(cells) + " |"
                    changed = True

                # elif current_table_mode == "main_parameter" and len(cells) >= 5:
                #     current_table_mode = "deviation"
                #     parameter_idx = _find_table_column(current_table_header, ("参数项", "技术参数项", "招标技术参数要求", "招标要求"))
                #     requirement_idx = _find_table_column(current_table_header, ("采购文件技术要求", "采购文件要求", "招标要求", "招标技术参数要求"))
                #     response_idx = _find_table_column(current_table_header, ("响应文件响应情况", "投标产品响应参数", "响应情况", "实际响应值"))
                #     deviation_idx = _find_table_column(current_table_header, ("偏离说明", "偏离情况"))
                #     parameter_cell = _safe_text(cells[parameter_idx]) if 0 <= parameter_idx < len(cells) else _safe_text(cells[1])
                #     if requirement_idx >= 0 and requirement_idx != parameter_idx:
                #         parameter_name = parameter_cell.split("：", 1)[0].strip()
                #         requirement_value = _safe_text(cells[requirement_idx]) if 0 <= requirement_idx < len(cells) else parameter_cell
                #     else:
                #         parameter_name = parameter_cell.split("：", 1)[0].strip()
                #         requirement_value = parameter_cell
                #     match = _find_technical_match(evidence_result, row_package_id, parameter_name)
                #     response_value = _resolve_materialized_response_value(product, match, parameter_name)
                #     evaluation = _evaluate_requirement_response(requirement_value, response_value)
                #     is_proven = bool(match and match.get("proven"))
                #     if 0 <= response_idx < len(cells):
                #         cells[response_idx] = response_value or "【待填写：实际响应值】"
                #     elif len(cells) >= 4:
                #         cells[3] = response_value or "【待填写：实际响应值】"
                #     if 0 <= deviation_idx < len(cells):
                #         cells[deviation_idx] = (
                #             _resolve_materialized_deviation_status(match, evaluation)
                #             if response_value and is_proven
                #             else "【待填写：无偏离/正偏离/负偏离】"
                #         )
                #     elif len(cells) >= 5:
                #         cells[4] = (
                #             _resolve_materialized_deviation_status(match, evaluation)
                #             if response_value and is_proven
                #             else "【待填写：无偏离/正偏离/负偏离】"
                #         )
                #     line = "| " + " | ".join(cells) + " |"
                #     changed = True
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
                elif current_table_mode == "review_detailed" and len(cells) >= 4:
                    item_idx = _find_table_column(current_table_header, ("评审项", "评审因素", "内容", "审查项"))
                    requirement_idx = _find_table_column(current_table_header, ("采购文件评分要求", "评分标准", "评审标准", "招标文件要求"))
                    response_idx = _find_table_column(current_table_header, ("响应文件对应内容", "响应情况"))
                    self_idx = _find_table_column(current_table_header, ("自评说明", "是否满足"))
                    evidence_idx = _find_table_column(current_table_header, ("证明材料/页码", "证明材料页码", "证明材料", "页码"))
                    item_name = _safe_text(cells[item_idx]) if 0 <= item_idx < len(cells) else ""
                    requirement = _safe_text(cells[requirement_idx]) if 0 <= requirement_idx < len(cells) else ""
                    location = _resolve_review_location(item_name, requirement, row_package_id)
                    review_match = _find_review_match(evidence_result, row_package_id, item_name, requirement)
                    evidence_ref = _resolve_review_evidence(
                        item_name,
                        requirement,
                        row_package_id,
                        company,
                        products,
                        evidence_result,
                    )
                    if 0 <= response_idx < len(cells) and (
                        not cells[response_idx].strip() or "待填写" in cells[response_idx]
                    ):
                        cells[response_idx] = location
                        changed = True
                    if 0 <= self_idx < len(cells) and (
                        not cells[self_idx].strip() or "待填写" in cells[self_idx]
                    ):
                        has_evidence_ref = bool(evidence_ref and evidence_ref != f"见{location}")
                        has_proven_fact = bool(
                            review_match and (
                                bool(review_match.get("proven"))
                                or _safe_text(review_match.get("deviation_status"), "") in {"无偏离", "有偏离"}
                            )
                        )
                        if has_proven_fact:
                            cells[self_idx] = "满足"
                        elif has_evidence_ref:
                            cells[self_idx] = "已形成章节定位，待核页码"
                        else:
                            cells[self_idx] = "待补证明材料/页码"
                        changed = True
                    if 0 <= evidence_idx < len(cells) and (
                        not cells[evidence_idx].strip() or "待填写" in cells[evidence_idx]
                    ):
                        cells[evidence_idx] = evidence_ref
                        changed = True
                    line = "| " + " | ".join(cells) + " |"
                elif current_table_mode == "review_page" and len(cells) >= 3:
                    item_idx = _find_table_column(current_table_header, ("审查内容", "审查项", "评审项", "评审因素", "内容"))
                    requirement_idx = _find_table_column(current_table_header, ("合格条件", "招标文件要求", "评审标准", "采购文件评分要求"))
                    page_idx = _find_table_column(current_table_header, ("投标文件所在页码", "投标文件对应页码", "对应材料/页码", "页码"))
                    item_name = _safe_text(cells[item_idx]) if 0 <= item_idx < len(cells) else ""
                    requirement = _safe_text(cells[requirement_idx]) if 0 <= requirement_idx < len(cells) else ""
                    page_ref = _resolve_review_evidence(
                        item_name,
                        requirement,
                        row_package_id,
                        company,
                        products,
                        evidence_result,
                    )
                    if 0 <= page_idx < len(cells) and (
                        not cells[page_idx].strip() or "待填写" in cells[page_idx]
                    ):
                        cells[page_idx] = page_ref
                        changed = True
                    line = "| " + " | ".join(cells) + " |"

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


def _profile_for_package(
    package_id: str | None,
    product_profiles: dict[str, dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """按包号获取对应产品画像。"""
    if not package_id or not product_profiles:
        return None
    return product_profiles.get(str(package_id))

def _materialize_sections(
    sections: list[BidDocumentSection],
    tender: TenderDocument,
    company: CompanyProfile | None,
    products: dict[str, ProductSpecification],
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[BidDocumentSection], dict[str, Any]]:
    """实装章节。"""
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
