from __future__ import annotations

import logging
import re
from typing import Any

import app.services.evidence_binder as _evidence_binder
import app.services.one_click_generator.common as _common
import app.services.requirement_processor as _requirement_processor
from app.schemas import ProcurementPackage
from app.services.evidence_binder import _extract_evidence_snippet, _is_dirty_evidence_snippet
from app.services.one_click_generator.common import (
    _HARD_REQUIREMENT_MARKERS,
    _MAX_TECH_ROWS_PER_PACKAGE,
    _PENDING_BIDDER_RESPONSE,
    _RICH_EXPANSION_MODE,
    _as_text,
    _safe_text,
)
from app.services.requirement_processor import (
    _effective_requirements,
    _extract_match_tokens,
    _extract_package_technical_scope_text,
    _is_bad_requirement_name,
    _is_bad_requirement_value,
    _markdown_cell,
    _package_forbidden_terms,
)

logger = logging.getLogger(__name__)

_STRUCTURED_NUMERIC_VALUE_PATTERN = re.compile(
    r"(?:≥|≤|>|<|>=|<=|不少于|不低于|不超过|至少|最高|最低)\s*\d+(?:\.\d+)?(?:\s*[%A-Za-z/\-._\u00b0\u03bc\u4e00-\u9fff]+)?$"
)
_EMPTYISH_TEXT_VALUES = {"", "none", "null", "nan", "n/a"}

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


def _is_usable_requirement_value(value: str) -> bool:
    text = _safe_text(value, "")
    if not text:
        return True
    if not _is_bad_requirement_value(text):
        return True
    return bool(_STRUCTURED_NUMERIC_VALUE_PATTERN.fullmatch(text))


def _normalized_optional_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in _EMPTYISH_TEXT_VALUES:
        return default
    return text or default


_SOFT_RESPONSE_MARKERS = (
    "响应，投标产品（",
    "响应，详见投标产品",
    "满足该项要求",
    "满足招标要求",
    "详见技术偏离表",
    "承诺满足",
    "配合验收",
    "交付时提供",
)

def _response_kind(value: Any) -> str:
    text = _normalized_optional_text(value, "")
    if not text:
        return "pending"

    pending_markers = (
        _PENDING_BIDDER_RESPONSE,
        "【待填写：投标产品实参】",
        "【待填写：实际响应值】",
        "待补充（投标产品实参）",
        "待核实（需填入投标产品实参）",
        "待核实",
        "待补证",
    )
    if any(marker in text for marker in pending_markers):
        return "pending"

    plain = text.replace("。", "").replace("；", "").strip()
    if plain in {"响应", "满足", "符合", "无偏离"}:
        return "soft"

    if any(marker in text for marker in _SOFT_RESPONSE_MARKERS):
        return "soft"

    if re.fullmatch(r"(?:响应|满足|符合)(?:招标要求|采购要求|该项要求)?", plain):
        return "soft"

    return "real"


def _has_real_bidder_response(value: Any) -> bool:
    return _response_kind(value) == "real"

def _mapping_confidence_for_row(
    pkg: ProcurementPackage,
    req_key: str,
    req_val: str,
    tender_quote: str,
) -> str:
    quote = _as_text(tender_quote)
    if not quote:
        return "none"

    if _is_dirty_evidence_snippet(quote):
        return "none"

    if "待定位" in quote or "未定位" in quote:
        return "none"

    if _is_bad_requirement_name(req_key) or not _is_usable_requirement_value(req_val):
        return "none"

    forbidden_terms = _package_forbidden_terms(pkg.item_name)
    if forbidden_terms and any(tok in quote for tok in forbidden_terms):
        return "none"

    business_hints = (
        "投标报价", "报价书", "预算", "履约保证金",
        "付款方式", "交货期", "商务条款", "资格审查",
    )
    if any(tok in quote for tok in business_hints):
        return "none"

    key_tokens = [t for t in _extract_match_tokens(req_key) if len(t) >= 2]
    val_tokens = [t for t in _extract_match_tokens(req_val) if len(t) >= 2]

    key_overlap = [t for t in key_tokens[:3] if t in quote]
    val_overlap = [t for t in val_tokens[:4] if t in quote]

    if not key_overlap:
        return "none"

    if val_tokens and not val_overlap:
        return "none"

    if len(quote) < max(8, len(req_key) + 2):
        return "weak"

    tail_noise = (
        "履约保证金", "付款方式", "交货期",
        "技术参数符合要求", "完成科室操作人员培训",
    )
    if any(tok in quote for tok in tail_noise):
        return "weak"

    return "high"


def _row_is_usable_for_package(
    pkg: ProcurementPackage,
    req_key: str,
    req_val: str,
    tender_quote: str = "",
    bidder_quote: str = "",
) -> bool:
    if _is_bad_requirement_name(req_key):
        return False
    if not _is_usable_requirement_value(req_val):
        return False

    forbidden_terms = _package_forbidden_terms(pkg.item_name)
    row_text = " ".join(x for x in [req_key, req_val, tender_quote, bidder_quote] if x)
    if forbidden_terms and any(tok in row_text for tok in forbidden_terms):
        return False

    return True

def _resolve_structured_response(
    *,
    req_key: str,
    req_val: str,
    match: dict[str, Any] | None,
    product: Any = None,
    product_profile: dict[str, Any] | None = None,
) -> str:
    """
    结构化表格场景下的响应值兜底顺序：
    1. evidence_result 直接命中的 response_value
    2. product_profile 中的精确/近似映射
    3. 旧有 _build_response_value(...) 的能力兜底
    4. 最后才退化为统一待核实占位符
    """
    response = _normalized_optional_text((match or {}).get("response_value"), "")
    if response:
        return response

    response = _lookup_profile_response_value(product_profile, req_key)
    if response:
        return response

    response = _build_response_value(req_val, req_key=req_key, product=product)
    if response and response != _PENDING_BIDDER_RESPONSE:
        return response

    return _PENDING_BIDDER_RESPONSE


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
            req_category = _safe_text(requirement.get("category"), "technical_requirement")
            req_key = _safe_text(
                requirement.get("param_name") or requirement.get("parameter_name"),
                "",
            )
            req_val = _normalized_optional_text(
                requirement.get("normalized_value") or requirement.get("raw_text") or requirement.get("source_text"),
                "",
            )
            if category_filter is None and req_category in {
                "service_requirement",
                "acceptance_requirement",
                "documentation_requirement",
                "commercial_requirement",
                "compliance_note",
                "attachment_requirement",
                "noise",
            }:
                continue
            if (
                category_filter is None
                and (
                req_category == "config_requirement"
                and any(token in req_key for token in ("装箱配置", "配置清单", "标准配置"))
                and any(marker in req_val for marker in ("详见招标文件", "详见采购文件", "按招标文件", "按采购文件"))
                )
            ):
                continue
            requirement_id = _safe_text(requirement.get("requirement_id"), "")
            match = match_by_id.get(requirement_id) or match_by_param.get(req_key)

            response = _resolve_structured_response(
                req_key=req_key,
                req_val=req_val,
                match=match,
                product=product,
                product_profile=product_profile,
            )

            bidder_source, bidder_quote, bidder_page = _format_structured_bidder_evidence(match, req_key, response)
            # 先拿 binding，再回退到 match / requirement，避免旧的 source_text 抢占更干净的 excerpt
            _tender_bind = (tender_bindings or {}).get(requirement_id, {})
            tender_quote = _safe_text(
                _tender_bind.get("source_excerpt")
                or (match or {}).get("tender_source_text")
                or requirement.get("source_excerpt")
                or requirement.get("source_text"),
                "",
            )
            tender_page = (
                _tender_bind.get("source_page")
                or (match or {}).get("tender_source_page")
                or requirement.get("source_page")
            )
            has_real_response = _has_real_bidder_response(response)
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
            _bid_bind = (bid_bindings or {}).get(requirement_id, {})
            if _tender_bind:
                tender_quote = _safe_text(_tender_bind.get("source_excerpt"), tender_quote)
                if not tender_page:
                    tender_page = _tender_bind.get("source_page")
            if not bidder_quote and _bid_bind:
                bidder_source = _safe_text(_bid_bind.get("evidence_file"), "") or bidder_source
                bidder_quote = _safe_text(_bid_bind.get("evidence_snippet"), "")
                bidder_page = _bid_bind.get("evidence_page") or bidder_page

            if _is_bad_requirement_name(req_key):
                continue
            if not _is_usable_requirement_value(req_val):
                continue

            forbidden_terms = _package_forbidden_terms(pkg.item_name)
            row_text = " ".join(x for x in [req_key, req_val, tender_quote, bidder_quote] if x)
            if forbidden_terms and any(tok in row_text for tok in forbidden_terms):
                continue

            mapping_confidence = _mapping_confidence_for_row(pkg, req_key, req_val, tender_quote)

            rows.append(
                {
                    "requirement_id": requirement_id,
                    "key": req_key,
                    "requirement": req_val,
                    "response": response,
                    "category": req_category,
                    "package_id": _safe_text(requirement.get("package_id"), pkg.package_id),
                    "evidence_source": bidder_source or "招标原文",
                    "evidence_quote": bidder_quote if (has_real_response and bidder_quote) else "",
                    "mapping_confidence": mapping_confidence,
                    "mapped": mapping_confidence == "high",
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
                        "待核实",
                    ),
                }
            )
        return rows, len(structured_requirements)

    requirements = _effective_requirements(pkg, tender_raw)
    package_scoped_raw = _extract_package_technical_scope_text(pkg, tender_raw)
    rows: list[dict[str, Any]] = []

    for req_key, req_val in requirements[:_MAX_TECH_ROWS_PER_PACKAGE]:
        if (
            any(token in req_key for token in ("装箱配置", "配置清单", "标准配置"))
            and any(marker in req_val for marker in ("详见招标文件", "详见采购文件", "按招标文件", "按采购文件"))
        ):
            continue
        source, quote, mapped = _extract_evidence_snippet(package_scoped_raw, req_key, req_val, tender_raw)
        response = _build_response_value(req_val, req_key=req_key, product=product)
        has_real_response = _has_real_bidder_response(response)

        bidder_evidence = ""
        bidder_source = ""
        if has_real_response and product is not None:
            bidder_source = "产品参数库"
            bidder_evidence = f"{req_key}：{response}"

        if not _row_is_usable_for_package(
                pkg,
                req_key,
                req_val,
                tender_quote=quote,
                bidder_quote=bidder_evidence,
        ):
            continue

        mapping_confidence = _mapping_confidence_for_row(pkg, req_key, req_val, quote)

        rows.append(
            {
                "requirement_id": "",
                "key": req_key,
                "requirement": req_val,
                "response": response,
                "category": category_filter or "technical_requirement",
                "package_id": pkg.package_id,
                "evidence_source": source or "招标原文",
                "evidence_quote": quote,
                "mapping_confidence": mapping_confidence,
                "mapped": mapping_confidence == "high",
                "has_real_response": has_real_response,
                "bidder_evidence": bidder_evidence,
                "bidder_evidence_source": bidder_source,
                "bidder_evidence_page": None,
                "source_page": None,
                "tender_quote": quote,
                "deviation_status": "待核实",
            }
        )

    return rows, len(requirements)

def _recommended_evidence_label(req_key: str, requirement: str = "") -> str:
    text = f"{_as_text(req_key)} {_as_text(requirement)}"

    if any(k in text for k in ("注册证", "备案凭证", "合格证", "说明书", "标签", "授权文件")):
        return "【待补证：注册证/备案凭证/说明书/标签/授权文件】"

    if any(k in text for k in ("室间质评", "能力验证", "检测报告", "质控品", "临检中心")):
        return "【待补证：室间质评报告/能力验证报告/检测报告】"

    if any(k in text for k in ("LIS", "分析软件", "审计追踪", "电子签名", "双向传输")):
        return "【待补证：软件说明书/功能截图/厂家说明】"

    if any(k in text for k in ("培训", "安装调试", "售后", "响应时间", "维护保养", "备用机")):
        return "【待补证：售后服务方案/培训计划/厂家承诺】"

    return "【待补证：说明书/彩页/厂家参数表】"

def _normalize_deviation_status(raw_value: Any, *, has_real: bool) -> str:
    text = _normalized_optional_text(raw_value, "")
    if not has_real:
        return "【待填写：无偏离/正偏离/负偏离】"
    bad_values = {
        "", "—", "-", "待填写", "【待填写】", "[待填写]", "待核实", "待补充", "待核对",
    }
    if text in bad_values or "待填写" in text:
        return "【待填写：无偏离/正偏离/负偏离】"
    return text


def _display_bidder_response(raw_value: Any) -> str:
    text = _normalized_optional_text(raw_value, "")
    if not text:
        return "【待填写：实际响应值】"

    pending_markers = (
        _PENDING_BIDDER_RESPONSE,
        "【待填写：投标产品实参】",
        "待补充（投标产品实参）",
        "待核实（需填入投标产品实参）",
    )
    if any(marker in text for marker in pending_markers):
        return "【待填写：实际响应值】"

    return text

def _display_model_cell(model_text: str, row_index: int) -> str:
    model_text = _safe_text(model_text, "")
    if model_text:
        return model_text if row_index == 1 else "同上"
    return "【待填写：投标型号】" if row_index == 1 else "同上"

def _build_deviation_table(
    tender,
    pkg,
    requirement_rows,
    total_requirements,
    product=None,
) -> str:
    def _safe(v):
        return _normalized_optional_text(v, "")

    def _md(v):
        return _safe(v).replace("|", "/")

    def _normalize_dev(raw_value, has_real=False):
        return _normalize_deviation_status(raw_value, has_real=has_real)

    p_model = ""
    p_mfr = ""
    if product is not None:
        p_model = _safe(getattr(product, "model", "")) or _safe(getattr(product, "product_name", ""))
        p_mfr = _safe(getattr(product, "manufacturer", ""))

    model_identity = " / ".join([x for x in [p_mfr, p_model] if x])

    lines = [
        "### 四、技术偏离及详细配置明细表",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        f"（第{pkg.package_id}包）",
        "| 序号 | 技术参数项 | 采购文件技术要求 | 响应文件响应情况 | 偏离情况 |",
        "|---:|---|---|---|---|",
    ]

    if not requirement_rows:
        lines.append(
            "| 1 | 【待填写：技术参数项】 | 【待人工根据采购文件逐条补录技术参数，禁止仅写“响应/完全响应”】 | 【待填写：品牌/型号/规格/配置及逐条响应】 | 【待填写：无偏离/正偏离/负偏离】 |"
        )
        lines.extend([
            "",
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：日期】",
        ])
        return "\n".join(lines)

    real_response_count = 0
    for idx, row in enumerate(requirement_rows, start=1):
        service_name = _md(row.get("key") or f"条款{idx}")
        tender_requirement = _md(row.get("requirement") or row.get("value") or "")

        raw_response = _safe(row.get("response"))
        has_real_response = _has_real_bidder_response(raw_response)
        if has_real_response:
            real_response_count += 1
        if not has_real_response:
            if model_identity:
                bid_response = f"品牌/型号：{_md(model_identity)}；【待填写：对应参数实测值/配置情况】"
            else:
                bid_response = "【待填写：品牌/型号/规格/配置及逐条响应】"
        else:
            if model_identity and model_identity not in raw_response:
                bid_response = f"品牌/型号：{_md(model_identity)}；{_md(raw_response)}"
            else:
                bid_response = _md(raw_response)

        deviation = _normalize_dev(row.get("deviation_status"), has_real=has_real_response)

        lines.append(
            f"| {idx} | {service_name} | {tender_requirement} | {bid_response} | {deviation} |"
        )

    if real_response_count == 0:
        lines.insert(
            4,
            "> 注：当前仅依据采购文件展开技术条款；未接入投标产品事实/证据时，响应值与偏离结论不得预填。",
        )

    if total_requirements > len(requirement_rows):
        lines.append("")
        lines.append(
            f"> 说明：当前已结构化生成 {len(requirement_rows)} / {total_requirements} 条，剩余条款必须继续按招标文件原文逐条补齐。"
        )

    lines.extend([
        "",
        "供应商全称：【待填写：投标人名称】",
        "日期：【待填写：年 月 日】",
    ])
    return "\n".join(lines)
