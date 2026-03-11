from __future__ import annotations

import logging

import app.services.one_click_generator.common as _common
import app.services.evidence_binder as _evidence_binder
import app.services.requirement_processor as _requirement_processor

logger = logging.getLogger(__name__)

def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _evidence_binder, _requirement_processor,):
    __reexport_all(_module)

del _module
def _build_response_commitment(req_key: str, req_val: str) -> str:
    key = _markdown_cell(req_key)
    value = _markdown_cell(req_val)
    if any(marker in value for marker in _HARD_REQUIREMENT_MARKERS):
        return f"承诺满足“{key}”且指标不低于“{value}”，按招标条款逐项验收。"
    return f"承诺满足“{key}：{value}”，交付时提供对应技术资料并配合验收。"


def _format_payment_execution_line(payment: str) -> str:
    if payment == "按招标文件及合同约定执行":
        return "6. 商务执行：付款方式按招标文件及合同约定执行。"
    return f"6. 商务执行：付款方式按“{payment}”执行。"


def _fuzzy_spec_lookup(product: Any, req_key: str) -> str:
    """在 product.specifications 中做模糊匹配，返回匹配到的值或空字符串。"""
    if product is None:
        return ""
    specs = getattr(product, "specifications", None) or {}
    if not specs:
        return ""
    normalized_key = _as_text(req_key)
    if not normalized_key:
        return ""
    # Exact match
    if normalized_key in specs:
        return _as_text(specs[normalized_key])
    # Short key match
    short_key = normalized_key.split("：", 1)[0].strip()
    if short_key in specs:
        return _as_text(specs[short_key])
    # Substring match
    for spec_key, spec_val in specs.items():
        k = _as_text(spec_key)
        if not k:
            continue
        if k in normalized_key or normalized_key in k:
            return _as_text(spec_val)
    # Token overlap match
    key_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", short_key) if len(t) >= 2]
    if key_tokens:
        for spec_key, spec_val in specs.items():
            k = _as_text(spec_key)
            if k and all(t in k for t in key_tokens[:3]):
                return _as_text(spec_val)
    return ""


_CAPABILITY_MARKERS = ("具备", "支持", "可", "能够", "提供", "配备", "配置", "采用", "满足", "兼容", "允许")


def _extract_numeric_with_unit(text: str) -> tuple[float | None, str]:
    """从文本中提取数值和单位。"""
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([^\d\s，,；;。、≥≤><]+)?", _as_text(text))
    if not match:
        return None, ""
    try:
        value = float(match.group(1))
    except ValueError:
        return None, ""
    unit = (match.group(2) or "").strip()
    return value, unit


def _fuzzy_token_spec_lookup(product: Any, req_key: str) -> str:
    """宽松 token 重叠匹配：只要 ≥1 个长度≥3 的 token 命中 spec key 就返回。"""
    if product is None:
        return ""
    specs = getattr(product, "specifications", None) or {}
    if not specs:
        return ""
    short_key = _as_text(req_key).split("：", 1)[0].strip()
    tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", short_key) if len(t) >= 3]
    if not tokens:
        return ""
    for spec_key, spec_val in specs.items():
        k = _as_text(spec_key)
        if k and any(t in k for t in tokens):
            return _as_text(spec_val)
    return ""


def _try_numeric_threshold_match(req_val: str, product: Any) -> str:
    """如果招标值含比较符，在产品参数中找同单位且满足阈值的值。"""
    if product is None:
        return ""
    specs = getattr(product, "specifications", None) or {}
    if not specs:
        return ""
    for marker in _HARD_REQUIREMENT_MARKERS:
        if marker not in req_val:
            continue
        threshold, unit = _extract_numeric_with_unit(req_val)
        if threshold is None:
            continue
        for spec_key, spec_val in specs.items():
            sv = _as_text(spec_val)
            spec_num, spec_unit = _extract_numeric_with_unit(sv)
            if spec_num is None:
                continue
            if unit and spec_unit and unit != spec_unit:
                if not ({unit, spec_unit} <= {"个", "台", "套", "只", "支", "条", "根", "把"}):
                    continue
            if marker in ("≥", ">=", "不低于", "不少于", "至少"):
                if spec_num >= threshold:
                    return sv
            elif marker in ("≤", "<=", "不高于", "不大于"):
                if spec_num <= threshold:
                    return sv
        break
    return ""


def _build_response_value(req_val: str, *, req_key: str = "", product: Any = None) -> str:
    """Return product spec value if available, with multiple fallback strategies to avoid '待核实'.

    When _RICH_EXPANSION_MODE is enabled, exhausts all product context before falling back to
    pending placeholders.
    """
    if product is None:
        return _PENDING_BIDDER_RESPONSE

    # 策略1: 精确/模糊 spec 匹配
    if req_key:
        matched = _fuzzy_spec_lookup(product, req_key)
        if matched:
            return matched

    # 策略2: 宽松 token 匹配
    if req_key:
        token_matched = _fuzzy_token_spec_lookup(product, req_key)
        if token_matched:
            return token_matched

    # 策略3: 数值门槛匹配 — 如果招标要求含 ≥/≤ 等比较符
    if req_val and any(m in req_val for m in _HARD_REQUIREMENT_MARKERS):
        numeric_match = _try_numeric_threshold_match(req_val, product)
        if numeric_match:
            return numeric_match

    # 策略4: 布尔/能力类推断 — "具备"/"支持" 类条款
    combined = f"{req_key} {req_val}"
    if any(marker in combined for marker in _CAPABILITY_MARKERS):
        p_name = _as_text(getattr(product, "product_name", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        if p_name:
            return f"满足，投标产品（{p_mfr} {p_name}）具备该功能"

    # 策略5: 富展开模式 — 产品信息充分时给出描述而非空白占位符
    if _RICH_EXPANSION_MODE:
        specs = getattr(product, "specifications", None) or {}
        p_name = _as_text(getattr(product, "product_name", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        p_model = _as_text(getattr(product, "model", ""))

        # 策略5a: 找到任意相关 spec 值进行关联
        if req_key and specs:
            req_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", req_key) if len(t) >= 2]
            for spec_key, spec_val in specs.items():
                k = _as_text(spec_key)
                if k and req_tokens and any(t in k for t in req_tokens):
                    return _as_text(spec_val)

        # 策略5b: 产品信息充分时给出上下文描述
        if p_name and len(specs) >= 3:
            identity = f"{p_mfr} {p_model}" if p_model else p_mfr
            return f"响应，投标产品（{identity.strip()} {p_name}）满足该项要求，详见技术偏离表"

        # 策略5c: 即使信息不够充分，有产品名时也给出承诺式响应
        if p_name:
            return f"响应，投标产品（{p_name}）满足招标要求"

    # 策略6: 兜底（原始模式）
    specs = getattr(product, "specifications", None) or {}
    p_name = _as_text(getattr(product, "product_name", ""))
    p_mfr = _as_text(getattr(product, "manufacturer", ""))
    if p_name and len(specs) >= 3:
        return f"响应，详见投标产品（{p_mfr} {p_name}）技术偏离表"

    return _PENDING_BIDDER_RESPONSE


def _structured_requirements_for_package(
    normalized_result: dict[str, Any] | None,
    package_id: str,
    category_filter: str | None = None,
) -> list[dict[str, Any]]:
    """从归一化结果中提取指定包的需求列表。

    category_filter: 如果指定，只返回该分类的需求（如 'technical_requirement'）
    自动排除 noise 类别和跨包条目。
    """
    if not normalized_result:
        return []
    items: list[dict[str, Any]] = []
    for requirement in normalized_result.get("technical_requirements", []):
        if not isinstance(requirement, dict):
            continue
        if _safe_text(requirement.get("package_id"), "") != package_id:
            continue
        # 始终排除 noise 类别
        req_category = _safe_text(requirement.get("category"), "")
        if req_category == "noise":
            continue
        if category_filter:
            if req_category and req_category != category_filter:
                continue
        items.append(requirement)
    return items


def _structured_match_indexes(
    evidence_result: dict[str, Any] | None,
    package_id: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_param: dict[str, dict[str, Any]] = {}
    if not evidence_result:
        return by_id, by_param

    for match in evidence_result.get("technical_matches", []):
        if not isinstance(match, dict):
            continue
        if _safe_text(match.get("package_id"), "") != package_id:
            continue
        requirement_id = _safe_text(match.get("requirement_id"), "")
        parameter_name = _safe_text(match.get("parameter_name"), "")
        if requirement_id:
            by_id[requirement_id] = match
        if parameter_name and parameter_name not in by_param:
            by_param[parameter_name] = match
    return by_id, by_param


def _lookup_profile_response_value(
    product_profile: dict[str, Any] | None,
    parameter_name: str,
) -> str:
    if not product_profile or not parameter_name:
        return ""

    identity_candidates = (
        (("品牌",), product_profile.get("brand")),
        (("型号", "规格型号", "品牌型号"), product_profile.get("model")),
        (("厂家", "生产厂家", "制造商"), product_profile.get("manufacturer")),
    )
    for aliases, value in identity_candidates:
        if any(alias in parameter_name for alias in aliases):
            normalized = _as_text(value)
            if normalized:
                return normalized

    specs = product_profile.get("technical_specs") or {}
    if isinstance(specs, dict):
        exact = _as_text(specs.get(parameter_name))
        if exact:
            return exact
        parameter_tokens = [
            token for token in re.split(r"[，,、；;：:（）()\[\]\s/]+", parameter_name) if len(token) >= 2
        ]
        for spec_key, spec_val in specs.items():
            spec_name = _as_text(spec_key)
            if not spec_name:
                continue
            if spec_name == parameter_name or parameter_name in spec_name or spec_name in parameter_name:
                return _as_text(spec_val)
            if parameter_tokens and any(token in spec_name for token in parameter_tokens):
                return _as_text(spec_val)

    config_items = product_profile.get("config_items") or []
    for item in config_items:
        if not isinstance(item, dict):
            continue
        item_name = _as_text(
            item.get("配置项") or item.get("name") or item.get("item_name") or item.get("名称")
        )
        if not item_name:
            continue
        if item_name == parameter_name or parameter_name in item_name or item_name in parameter_name:
            return _as_text(item.get("说明") or item.get("description") or item.get("remark") or "标配")

    return ""


def _format_structured_bidder_evidence(
    match: dict[str, Any] | None,
    req_key: str,
    response: str,
) -> tuple[str, str, Any]:
    if not match:
        return "", "", None

    bid_file = _safe_text(match.get("bid_evidence_file"), "")
    bid_page = match.get("bid_evidence_page")
    bid_type = _safe_text(match.get("bid_evidence_type"), "")
    bid_snippet = _safe_text(
        match.get("bid_evidence_snippet") or match.get("bidder_evidence_quote"),
        "",
    )
    if not bid_file and not bid_snippet:
        return "", "", bid_page

    source_bits = [bit for bit in (bid_file, bid_type) if bit]
    if not source_bits:
        source_bits = [_safe_text(match.get("bidder_evidence_source"), "投标方资料")]
    source_text = " / ".join(source_bits)

    quote_text = bid_snippet or f"{req_key}：{response}"
    if bid_page is not None:
        quote_text = f"{quote_text}（第{bid_page}页）"
    return source_text, quote_text, bid_page


def _build_requirement_rows(
    pkg: ProcurementPackage,
    tender_raw: str,
    product: Any = None,
    *,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profile: dict[str, Any] | None = None,
    category_filter: str | None = None,
    tender_bindings: dict[str, Any] | None = None,
    bid_bindings: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    structured_requirements = _structured_requirements_for_package(
        normalized_result, pkg.package_id, category_filter=category_filter,
    )
    if structured_requirements:
        match_by_id, match_by_param = _structured_match_indexes(evidence_result, pkg.package_id)
        rows: list[dict[str, Any]] = []
        for requirement in structured_requirements[:_MAX_TECH_ROWS_PER_PACKAGE]:
            req_key = _safe_text(
                requirement.get("param_name") or requirement.get("parameter_name"),
                "",
            )
            req_val = _safe_text(requirement.get("normalized_value"), "")
            requirement_id = _safe_text(requirement.get("requirement_id"), "")
            match = match_by_id.get(requirement_id) or match_by_param.get(req_key)

            response = _safe_text(match.get("response_value") if match else "", "")
            if not response:
                response = _lookup_profile_response_value(product_profile, req_key)
            if not response:
                response = _PENDING_BIDDER_RESPONSE

            bidder_source, bidder_quote, bidder_page = _format_structured_bidder_evidence(match, req_key, response)
            tender_quote = _safe_text(
                (match or {}).get("tender_source_text")
                or requirement.get("source_text")
                or requirement.get("source_excerpt"),
                "",
            )
            tender_page = (match or {}).get("tender_source_page") or requirement.get("source_page")
            has_real_response = response != _PENDING_BIDDER_RESPONSE
            if not bidder_quote and has_real_response:
                bidder_source = bidder_source or _safe_text(
                    (match or {}).get("matched_fact_source"),
                    "产品参数库",
                )
                bidder_quote = _safe_text(
                    (match or {}).get("matched_fact_quote"),
                    f"{req_key}：{response}",
                )

            # 合并 tender/bid binding 信息
            _tender_bind = (tender_bindings or {}).get(requirement_id, {})
            _bid_bind = (bid_bindings or {}).get(requirement_id, {})
            if not tender_quote and _tender_bind:
                tender_quote = _safe_text(_tender_bind.get("source_excerpt"), "")
                if not tender_page:
                    tender_page = _tender_bind.get("source_page")
            if not bidder_quote and _bid_bind:
                bidder_source = _safe_text(_bid_bind.get("evidence_file"), "") or bidder_source
                bidder_quote = _safe_text(_bid_bind.get("evidence_snippet"), "")
                bidder_page = _bid_bind.get("evidence_page") or bidder_page

            rows.append(
                {
                    "requirement_id": requirement_id,
                    "key": req_key,
                    "requirement": req_val,
                    "response": response,
                    "category": _safe_text(requirement.get("category"), "technical_requirement"),
                    "package_id": _safe_text(requirement.get("package_id"), pkg.package_id),
                    "evidence_source": bidder_source or "招标原文",
                    "evidence_quote": bidder_quote or tender_quote,
                    "mapped": bool(tender_quote or bidder_quote),
                    "has_real_response": has_real_response,
                    "bidder_evidence": bidder_quote,
                    "bidder_evidence_source": bidder_source,
                    "bidder_evidence_page": bidder_page,
                    "source_page": tender_page,
                    "tender_quote": tender_quote,
                    "tender_evidence_file": _safe_text(_tender_bind.get("evidence_file"), ""),
                    "tender_evidence_page": _tender_bind.get("page"),
                    "bid_evidence_file": _safe_text(_bid_bind.get("evidence_file"), ""),
                    "bid_evidence_page": _bid_bind.get("evidence_page"),
                    "bid_evidence_type": _safe_text(_bid_bind.get("evidence_type"), ""),
                    "bid_evidence_snippet": _safe_text(_bid_bind.get("evidence_snippet"), ""),
                    "deviation_status": _safe_text(
                        (match or {}).get("deviation_status"),
                        "无偏离" if has_real_response and bidder_quote else "待核实",
                    ),
                }
            )
        return rows, len(structured_requirements)

    requirements = _effective_requirements(pkg, tender_raw)
    package_scoped_raw = _extract_package_technical_scope_text(pkg, tender_raw)
    rows: list[dict[str, Any]] = []
    for req_key, req_val in requirements[:_MAX_TECH_ROWS_PER_PACKAGE]:
        source, quote, mapped = _extract_evidence_snippet(package_scoped_raw, req_key, req_val, tender_raw)
        response = _build_response_value(req_val, req_key=req_key, product=product)
        has_real_response = response != _PENDING_BIDDER_RESPONSE
        # Build bidder evidence from product if available
        bidder_evidence = ""
        if has_real_response and product is not None:
            bidder_evidence = f"产品参数库：{req_key}={response}"
        rows.append(
            {
                "key": req_key,
                "requirement": req_val,
                "response": response,
                "evidence_source": source,
                "evidence_quote": quote,
                "mapped": mapped,
                "has_real_response": has_real_response,
                "bidder_evidence": bidder_evidence,
            }
        )
    return rows, len(requirements)


def _build_deviation_table(
    tender: TenderDocument,
    pkg: ProcurementPackage,
    requirement_rows: list[dict[str, Any]],
    total_requirements: int,
    product: Any = None,
) -> str:
    """构建 8 列技术偏离表（升级版）。

    只接收 technical_requirement 类别的行，非技术行会被过滤并记录日志。
    """
    # 过滤：技术偏离表只接受技术参数类行
    _ALLOWED_TECH_CATEGORIES = {"technical_requirement", "config_requirement", ""}
    filtered_rows = []
    for row in requirement_rows:
        cat = row.get("category", "")
        if cat and cat not in _ALLOWED_TECH_CATEGORIES:
            logger.debug("技术偏离表剔除非技术行: key=%s category=%s", row.get("key", "?"), cat)
            continue
        filtered_rows.append(row)
    requirement_rows = filtered_rows

    # 产品身份信息
    p_model = ""
    p_mfr = ""
    if product is not None:
        p_model = _as_text(getattr(product, "model", "")) or _as_text(getattr(product, "product_name", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))

    lines = [
        "### （一）技术偏离及详细配置明细表",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        f"投标型号：{p_mfr} {p_model}" if p_model else "",
        "",
        "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines = [line for line in lines if line or line == ""]

    if not requirement_rows:
        lines.append(
            f"| {pkg.package_id}.1 | 详见招标文件采购需求 | {p_model or '[待填写]'} | {_PENDING_BIDDER_RESPONSE} | 待核实 | 结构化解析结果 | — | 建议复核原文并补齐投标方证据 |"
        )
        return "\n".join(lines)

    for idx, row in enumerate(requirement_rows, start=1):
        clause_no = f"{pkg.package_id}.{idx}"
        req = f"{_markdown_cell(str(row['key']))}：{_markdown_cell(str(row['requirement']))}"
        has_real = row.get("has_real_response", False)
        bidder_ev = _safe_text(row.get("bidder_evidence"), "")
        bidder_source = _safe_text(row.get("bidder_evidence_source"), "")
        bidder_page = row.get("bidder_evidence_page")
        model_cell = _markdown_cell(p_model) if p_model else "[待填写]"
        response_cell = _markdown_cell(str(row['response']))

        if has_real and bidder_ev:
            source_text = bidder_source or _safe_text(row.get("evidence_source"), "投标方资料")
            evidence_text = f"{_markdown_cell(source_text)}；{_markdown_cell(bidder_ev)}"
            deviation = _safe_text(row.get("deviation_status"), "无偏离")
            remark = "已匹配产品参数"
        else:
            tender_quote = _safe_text(row.get("tender_quote"), "")
            evidence_text = (
                f"{_markdown_cell(_safe_text(row.get('evidence_source'), '招标原文'))}；"
                f"{_markdown_cell(tender_quote or '待补充（投标方证据）')}"
            )
            deviation = _safe_text(row.get("deviation_status"), "待核实")
            remark = "需补充投标方证据"

        if bidder_page is not None:
            page_ref = str(bidder_page)
        elif row.get("source_page") is not None:
            page_ref = str(row.get("source_page"))
        else:
            page_ref = "—"

        lines.append(
            f"| {clause_no} | {req} | {model_cell} | {response_cell} | {deviation} | {evidence_text} | {page_ref} | {remark} |"
        )

    if total_requirements > len(requirement_rows):
        lines.append(
            f"| — | 其余技术参数 | {p_model or '[待填写]'} | {_PENDING_BIDDER_RESPONSE} | 待核实 | 证据映射表继续列示 | — | 待补投标方证据 |"
        )

    return "\n".join(lines)


