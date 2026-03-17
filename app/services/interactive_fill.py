from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.schemas import (
    BidDocumentSection,
    CompanyProfile,
    OneClickInteractivePrompt,
)


_PLACEHOLDER_RE = re.compile(r"【(?P<prefix>待填写|待补证|待上传|待确认|待定位)(?:[:：](?P<label>[^】]+))?】")

_SELECT_OPTIONS = {
    "yes_no": ["是", "否"],
    "pass_fail": ["通过", "不通过"],
    "match_unmatch": ["满足", "不满足"],
    "fit_unfit": ["符合", "不符合"],
    "deviation": ["无偏离", "正偏离", "负偏离"],
}

_CANONICAL_LABELS = {
    "company_name": "投标人名称",
    "legal_representative": "法定代表人",
    "authorized_representative": "授权代表",
    "company_phone": "联系电话",
    "company_address": "联系地址",
    "document_date": "日期",
    "product_brand": "品牌",
    "product_model": "投标型号",
    "product_origin": "产地",
    "manufacturer": "生产厂家",
    "product_identity": "品牌型号",
    "product_identity_origin": "品牌/型号，产地",
}

_REUSABLE_FIELD_KEYS = {
    "company_name",
    "legal_representative",
    "authorized_representative",
    "company_phone",
    "company_address",
    "document_date",
    "product_brand",
    "product_model",
    "product_origin",
    "manufacturer",
    "product_identity",
    "product_identity_origin",
}

_HARD_MANUAL_TOKENS = (
    "报价",
    "总价",
    "单价",
    "预算",
    "页码",
    "参数值",
    "响应值",
    "逐条响应",
    "如何满足",
    "技术参数项",
    "证据",
    "材料名称",
    "偏离",
)

logger = logging.getLogger(__name__)


def _normalize_label(label: str) -> str:
    text = re.sub(r"\s+", " ", str(label or "")).strip()
    text = text.strip("：:")
    return text or "待补内容"


def _field_key_for_label(label: str) -> str:
    normalized = _normalize_label(label)

    if any(token in normalized for token in ("投标人名称", "供应商全称", "承诺方名称")):
        return "company_name"
    if "法定代表人" in normalized:
        return "legal_representative"
    if "授权代表" in normalized:
        return "authorized_representative"
    if normalized in {"联系电话", "电话"} or "联系电话" in normalized:
        return "company_phone"
    if any(token in normalized for token in ("联系地址", "详细通讯地址", "公司注册地址", "单位地址")):
        return "company_address"
    if any(token in normalized for token in ("日期", "年 月 日", "磋商日期", "谈判日期")):
        return "document_date"
    if normalized == "品牌":
        return "product_brand"
    if normalized in {"投标型号", "型号"}:
        return "product_model"
    if normalized == "产地":
        return "product_origin"
    if any(token in normalized for token in ("生产厂家", "制造商名称")):
        return "manufacturer"
    if any(token in normalized for token in ("品牌/型号，产地", "品牌/型号/产地")):
        return "product_identity_origin"
    if any(token in normalized for token in ("品牌型号", "品牌/型号")):
        return "product_identity"

    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:10]
    return f"custom_{digest}"


def _canonical_label(field_key: str, label: str) -> str:
    return _CANONICAL_LABELS.get(field_key, _normalize_label(label))


def _choices_for_label(label: str) -> list[str]:
    normalized = _normalize_label(label)
    if "是/否" in normalized:
        return _SELECT_OPTIONS["yes_no"]
    if "通过/不通过" in normalized:
        return _SELECT_OPTIONS["pass_fail"]
    if "满足/不满足" in normalized:
        return _SELECT_OPTIONS["match_unmatch"]
    if "符合/不符合" in normalized:
        return _SELECT_OPTIONS["fit_unfit"]
    if "无偏离/正偏离/负偏离" in normalized:
        return _SELECT_OPTIONS["deviation"]
    return []


def _prompt_type(prefix: str, label: str) -> str:
    normalized = _normalize_label(label)
    if _choices_for_label(normalized):
        return "select"
    if any(token in normalized for token in ("日期", "年 月 日", "磋商日期", "谈判日期")):
        return "date"
    if any(token in normalized for token in ("说明", "如何满足", "逐条响应", "配置清单", "关系说明", "服务承诺")):
        return "textarea"
    if prefix in {"待补证", "待上传", "待定位"}:
        return "textarea"
    return "text"


def _help_text(prefix: str, label: str) -> str:
    normalized = _normalize_label(label)
    if prefix == "待补证":
        return "填写可引用的材料名称、页码或说明。"
    if prefix == "待上传":
        return "填写上传材料名称，或在页面中导入答案 JSON 后自动回填。"
    if prefix == "待确认":
        return "该项需要人工判断后确认。"
    if prefix == "待定位":
        return "填写对应位置、页码或原文摘录。"
    if any(token in normalized for token in ("报价", "总价", "单价", "预算")):
        return "金额建议直接填写数字或含税金额说明。"
    return ""


def _is_reusable_prompt(prefix: str, field_key: str, label: str) -> bool:
    normalized = _normalize_label(label)
    if field_key not in _REUSABLE_FIELD_KEYS:
        return False
    if prefix in {"待补证", "待上传", "待确认", "待定位"}:
        return False
    if any(token in normalized for token in _HARD_MANUAL_TOKENS):
        return False
    return True


def _is_hard_manual_placeholder(prefix: str, label: str) -> bool:
    normalized = _normalize_label(label)
    if prefix in {"待补证", "待上传", "待确认", "待定位"}:
        return True
    return any(token in normalized for token in _HARD_MANUAL_TOKENS)


def _make_custom_field_key(label: str) -> str:
    normalized = _normalize_label(label)
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:10]
    return f"custom_{digest}"


def _llm_message_to_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return str(content).strip()


def _extract_json_payload(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    if not raw:
        return {}
    return json.loads(raw)


def _candidate_snippet(content: str, token: str) -> str:
    for line in (content or "").splitlines():
        if token in line:
            return re.sub(r"\s+", " ", line).strip()[:160]
    compact = re.sub(r"\s+", " ", content or "").strip()
    return compact[:160]


def _collect_placeholder_candidates(
    sections: list[BidDocumentSection],
) -> list[dict[str, Any]]:
    aggregated: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for section in sections:
        section_title = (section.section_title or "").strip() or "未命名章节"
        content = section.content or ""
        for match in _PLACEHOLDER_RE.finditer(content):
            prefix = match.group("prefix") or "待填写"
            label = _normalize_label(match.group("label") or prefix)
            token = match.group(0)
            candidate_key = f"{prefix}::{label}"
            if candidate_key not in aggregated:
                field_key_guess = _field_key_for_label(label)
                aggregated[candidate_key] = {
                    "candidate_id": f"cand_{len(aggregated) + 1}",
                    "prefix": prefix,
                    "label": label,
                    "field_key_guess": field_key_guess,
                    "canonical_label_guess": _canonical_label(field_key_guess, label),
                    "rule_hint_reusable": _is_reusable_prompt(prefix, field_key_guess, label),
                    "placeholder_tokens": [],
                    "occurrence_count": 0,
                    "section_titles": [],
                    "examples": [],
                }

            candidate = aggregated[candidate_key]
            candidate["occurrence_count"] += 1
            if token not in candidate["placeholder_tokens"]:
                candidate["placeholder_tokens"].append(token)
            if section_title not in candidate["section_titles"]:
                candidate["section_titles"].append(section_title)
            snippet = _candidate_snippet(content, token)
            if snippet and snippet not in candidate["examples"] and len(candidate["examples"]) < 3:
                candidate["examples"].append(snippet)

    return list(aggregated.values())


def _fallback_candidate_decisions(
    candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    decisions: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        prefix = str(candidate.get("prefix") or "")
        label = str(candidate.get("label") or "")
        guess = str(candidate.get("field_key_guess") or "")
        reusable = _is_reusable_prompt(prefix, guess, label)
        if reusable:
            field_key = guess if guess in _REUSABLE_FIELD_KEYS else _make_custom_field_key(label)
            canonical_label = (
                _CANONICAL_LABELS[field_key]
                if field_key in _CANONICAL_LABELS
                else _normalize_label(label)
            )
            decisions[str(candidate["candidate_id"])] = {
                "reusable": True,
                "field_key": field_key,
                "canonical_label": canonical_label,
                "reason": "fallback_rule",
            }
        else:
            decisions[str(candidate["candidate_id"])] = {
                "reusable": False,
                "field_key": "",
                "canonical_label": _normalize_label(label),
                "reason": "fallback_rule",
            }
    return decisions


def _classify_candidates_with_llm(
    candidates: list[dict[str, Any]],
    llm: Any | None = None,
) -> dict[str, dict[str, Any]]:
    if not candidates or llm is None or not hasattr(llm, "invoke"):
        return {}

    allowed_keys = sorted(_REUSABLE_FIELD_KEYS)
    system_prompt = (
        "你是“招投标文档交互字段判定器”。\n"
        "目标：判断哪些占位符适合在网页中只问一次并复用于全文，哪些必须留在文档中由用户自行逐项填写。\n"
        "只允许把“共享事实”判定为 reusable，例如：投标人名称、法定代表人、授权代表、联系电话、地址、日期、品牌、型号、产地、生产厂家、品牌型号组合。\n"
        "必须判定为 non_reusable 的典型内容：报价/总价/单价、页码、证据、逐条响应、参数值、实际响应值、偏离情况、表格行级字段、逐项满足/不满足。\n"
        f"如果 reusable_key 选择标准键，只能从以下列表中选：{', '.join(allowed_keys)}。\n"
        "如果是可复用但不属于标准键，reusable_key 填 custom，并给出简洁 canonical_label。\n"
        "如果不可复用，reusable=false，reusable_key 置空。\n"
        "返回严格 JSON："
        '{"items":[{"candidate_id":"cand_1","reusable":true,"reusable_key":"company_name","canonical_label":"投标人名称","reason":"共享主体信息"}]}'
    )

    candidate_view = [
        {
            "candidate_id": candidate["candidate_id"],
            "prefix": candidate["prefix"],
            "label": candidate["label"],
            "occurrence_count": candidate["occurrence_count"],
            "section_titles": candidate["section_titles"],
            "examples": candidate["examples"],
            "field_key_guess": candidate["field_key_guess"],
            "rule_hint_reusable": candidate["rule_hint_reusable"],
        }
        for candidate in candidates
    ]
    user_prompt = (
        "请逐项判定以下占位符候选是否适合进入网页交互。\n"
        "同义字段应尽量映射到同一个 reusable_key 或同一个 canonical_label。\n"
        "候选列表：\n"
        f"{json.dumps(candidate_view, ensure_ascii=False, indent=2)}"
    )

    try:
        response = llm.invoke([SystemMessage(system_prompt), HumanMessage(user_prompt)])
        payload = _extract_json_payload(_llm_message_to_text(response))
    except Exception as exc:  # noqa: BLE001
        logger.warning("交互字段模型判定失败，回退规则判定：%s", exc)
        return {}

    items = payload.get("items")
    if not isinstance(items, list):
        return {}

    decisions: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        reusable = bool(item.get("reusable"))
        reusable_key = str(item.get("reusable_key") or "").strip()
        canonical_label = _normalize_label(str(item.get("canonical_label") or ""))
        reason = str(item.get("reason") or "").strip()
        decisions[candidate_id] = {
            "reusable": reusable,
            "field_key": reusable_key,
            "canonical_label": canonical_label,
            "reason": reason,
        }
    return decisions


def _resolve_candidate_decisions(
    candidates: list[dict[str, Any]],
    llm_decisions: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    fallback = _fallback_candidate_decisions(candidates)
    resolved: dict[str, dict[str, Any]] = {}

    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        label = str(candidate.get("label") or "")
        prefix = str(candidate.get("prefix") or "")
        guess = str(candidate.get("field_key_guess") or "")
        raw = llm_decisions.get(candidate_id)

        if _is_hard_manual_placeholder(prefix, label):
            resolved[candidate_id] = {
                "reusable": False,
                "field_key": "",
                "canonical_label": _normalize_label(label),
                "reason": "hard_manual_guard",
            }
            continue

        if not raw:
            resolved[candidate_id] = fallback[candidate_id]
            continue

        if not raw.get("reusable"):
            resolved[candidate_id] = {
                "reusable": False,
                "field_key": "",
                "canonical_label": _normalize_label(label),
                "reason": raw.get("reason") or "llm_non_reusable",
            }
            continue

        reusable_key = str(raw.get("field_key") or "").strip()
        canonical_label = _normalize_label(str(raw.get("canonical_label") or label))

        if reusable_key in _REUSABLE_FIELD_KEYS:
            resolved[candidate_id] = {
                "reusable": True,
                "field_key": reusable_key,
                "canonical_label": _CANONICAL_LABELS.get(reusable_key, canonical_label),
                "reason": raw.get("reason") or "llm_standard_key",
            }
            continue

        if reusable_key == "custom":
            resolved[candidate_id] = {
                "reusable": True,
                "field_key": _make_custom_field_key(canonical_label),
                "canonical_label": canonical_label,
                "reason": raw.get("reason") or "llm_custom_key",
            }
            continue

        if guess in _REUSABLE_FIELD_KEYS and not _is_hard_manual_placeholder(prefix, label):
            resolved[candidate_id] = {
                "reusable": True,
                "field_key": guess,
                "canonical_label": _CANONICAL_LABELS.get(guess, canonical_label),
                "reason": raw.get("reason") or "llm_guess_key",
            }
            continue

        resolved[candidate_id] = fallback[candidate_id]

    return resolved


def plan_interactive_fill(
    sections: list[BidDocumentSection],
    llm: Any | None = None,
) -> dict[str, Any]:
    """使用模型优先、规则兜底的方式规划交互式填写项。"""
    candidates = _collect_placeholder_candidates(sections)
    llm_decisions = _classify_candidates_with_llm(candidates, llm=llm)
    decisions = _resolve_candidate_decisions(candidates, llm_decisions)

    reusable_groups: OrderedDict[str, dict[str, Any]] = OrderedDict()
    manual_items: OrderedDict[str, dict[str, Any]] = OrderedDict()
    annotated_sections: list[BidDocumentSection] = []

    candidate_by_token: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        decision = decisions.get(str(candidate["candidate_id"]), {})
        candidate["decision"] = decision
        for token in candidate.get("placeholder_tokens", []):
            candidate_by_token[str(token)] = candidate

        if decision.get("reusable"):
            group_key = str(decision["field_key"])
            if group_key not in reusable_groups:
                reusable_groups[group_key] = {
                    "field_key": group_key,
                    "label": str(decision.get("canonical_label") or candidate["label"]),
                    "prompt_type": _prompt_type(str(candidate["prefix"]), str(decision.get("canonical_label") or candidate["label"])),
                    "choices": _choices_for_label(str(decision.get("canonical_label") or candidate["label"])),
                    "required": True,
                    "help_text": _help_text(str(candidate["prefix"]), str(decision.get("canonical_label") or candidate["label"])),
                    "occurrence_count": 0,
                    "section_titles": [],
                    "aliases": [],
                    "placeholder_tokens": [],
                }
            group = reusable_groups[group_key]
            group["occurrence_count"] += int(candidate.get("occurrence_count") or 0)
            for section_title in candidate.get("section_titles", []):
                if section_title not in group["section_titles"]:
                    group["section_titles"].append(section_title)
            if candidate["label"] not in group["aliases"]:
                group["aliases"].append(candidate["label"])
            if group["label"] not in group["aliases"]:
                group["aliases"].append(group["label"])
            for token in candidate.get("placeholder_tokens", []):
                if token not in group["placeholder_tokens"]:
                    group["placeholder_tokens"].append(token)
        else:
            manual_label = str(decision.get("canonical_label") or candidate["label"])
            if manual_label not in manual_items:
                manual_items[manual_label] = {
                    "label": manual_label,
                    "count": 0,
                    "section_titles": [],
                }
            item = manual_items[manual_label]
            item["count"] += int(candidate.get("occurrence_count") or 0)
            for section_title in candidate.get("section_titles", []):
                if section_title not in item["section_titles"]:
                    item["section_titles"].append(section_title)

    for section in sections:
        content = section.content or ""

        def repl(match: re.Match[str]) -> str:
            token = match.group(0)
            candidate = candidate_by_token.get(token)
            if not candidate:
                return token
            decision = candidate.get("decision") or {}
            if decision.get("reusable"):
                return token
            manual_label = str(decision.get("canonical_label") or candidate.get("label") or "待补内容")
            return f"【请在文档中自行填写：{manual_label}】"

        annotated_sections.append(section.model_copy(update={"content": _PLACEHOLDER_RE.sub(repl, content)}))

    return {
        "sections": annotated_sections,
        "prompts": list(reusable_groups.values()),
        "manual_items": list(manual_items.values()),
        "candidates": candidates,
        "llm_decisions": llm_decisions,
    }


def annotate_manual_placeholders(
    sections: list[BidDocumentSection],
) -> tuple[list[BidDocumentSection], list[dict[str, Any]]]:
    """将不可复用的占位符改写为“文档中自行填写”，并返回汇总信息。"""

    manual_items: OrderedDict[str, dict[str, Any]] = OrderedDict()
    annotated_sections: list[BidDocumentSection] = []

    for section in sections:
        section_title = (section.section_title or "").strip() or "未命名章节"

        def repl(match: re.Match[str]) -> str:
            prefix = match.group("prefix") or "待填写"
            label = _normalize_label(match.group("label") or prefix)
            field_key = _field_key_for_label(label)
            if _is_reusable_prompt(prefix, field_key, label):
                return match.group(0)

            if label not in manual_items:
                manual_items[label] = {
                    "label": label,
                    "count": 0,
                    "section_titles": [],
                }
            item = manual_items[label]
            item["count"] += 1
            if section_title not in item["section_titles"]:
                item["section_titles"].append(section_title)
            return f"【请在文档中自行填写：{label}】"

        content = _PLACEHOLDER_RE.sub(repl, section.content or "")
        annotated_sections.append(section.model_copy(update={"content": content}))

    return annotated_sections, list(manual_items.values())


def extract_interactive_prompts(
    sections: list[BidDocumentSection],
) -> list[dict[str, Any]]:
    """从可编辑章节中抽取去重后的交互式待填写项。"""
    aggregated: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for section in sections:
        section_title = (section.section_title or "").strip() or "未命名章节"
        for match in _PLACEHOLDER_RE.finditer(section.content or ""):
            prefix = match.group("prefix") or "待填写"
            label = _normalize_label(match.group("label") or prefix)
            field_key = _field_key_for_label(label)
            if not _is_reusable_prompt(prefix, field_key, label):
                continue
            exact_token = match.group(0)

            if field_key not in aggregated:
                aggregated[field_key] = {
                    "field_key": field_key,
                    "label": _canonical_label(field_key, label),
                    "prompt_type": _prompt_type(prefix, label),
                    "choices": _choices_for_label(label),
                    "required": prefix not in {"待确认", "待定位"},
                    "help_text": _help_text(prefix, label),
                    "occurrence_count": 0,
                    "section_titles": [],
                    "aliases": [],
                    "placeholder_tokens": [],
                }

            prompt = aggregated[field_key]
            prompt["occurrence_count"] += 1
            if section_title not in prompt["section_titles"]:
                prompt["section_titles"].append(section_title)
            if label not in prompt["aliases"]:
                prompt["aliases"].append(label)
            if exact_token not in prompt["placeholder_tokens"]:
                prompt["placeholder_tokens"].append(exact_token)

    return list(aggregated.values())


def serialize_interactive_prompts(
    prompts: list[dict[str, Any]],
) -> list[OneClickInteractivePrompt]:
    """把内部提示项结构转成接口响应模型。"""
    serialized: list[OneClickInteractivePrompt] = []
    for item in prompts:
        serialized.append(
            OneClickInteractivePrompt(
                field_key=item["field_key"],
                label=item["label"],
                prompt_type=item["prompt_type"],
                choices=list(item.get("choices", [])),
                required=bool(item.get("required", True)),
                help_text=str(item.get("help_text", "")),
                occurrence_count=int(item.get("occurrence_count", 1)),
                section_titles=list(item.get("section_titles", [])),
                aliases=list(item.get("aliases", [])),
            )
        )
    return serialized


def _answer_lookup(
    prompt: dict[str, Any],
    answers: dict[str, Any],
) -> str:
    field_key = str(prompt.get("field_key") or "").strip()
    label = str(prompt.get("label") or "").strip()
    aliases = [str(alias).strip() for alias in prompt.get("aliases", []) if str(alias).strip()]

    for key in [field_key, label, *aliases]:
        value = answers.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    normalized_answers = {str(key).strip(): str(value).strip() for key, value in answers.items() if str(value).strip()}
    brand = normalized_answers.get("product_brand", "")
    model = normalized_answers.get("product_model", "") or normalized_answers.get("product_identity", "")
    origin = normalized_answers.get("product_origin", "")

    if field_key == "product_identity":
        if brand and model:
            return f"{brand} {model}".strip()
        return model or brand

    if field_key == "product_identity_origin":
        identity = ""
        if brand and model:
            identity = f"{brand} {model}".strip()
        else:
            identity = model or brand
        if identity and origin:
            return f"{identity}，{origin}"
        return identity or origin

    if field_key == "product_model":
        return model

    if field_key == "manufacturer":
        return normalized_answers.get("manufacturer", "")

    if field_key == "document_date":
        return normalized_answers.get("document_date", "")

    return ""


def build_company_from_answers(answers: dict[str, Any]) -> CompanyProfile:
    """根据交互答案构造用于封面的企业信息。"""
    normalized = {str(key).strip(): str(value).strip() for key, value in answers.items() if str(value).strip()}
    return CompanyProfile(
        company_id="interactive",
        name=normalized.get("company_name") or normalized.get("投标人名称") or "【待填写：投标人名称】",
        legal_representative=(
            normalized.get("legal_representative")
            or normalized.get("法定代表人")
            or "【待填写：法定代表人】"
        ),
        address=(
            normalized.get("company_address")
            or normalized.get("联系地址")
            or "【待填写：公司注册地址】"
        ),
        phone=normalized.get("company_phone") or normalized.get("联系电话") or "【待填写：联系电话】",
        document_date=normalized.get("document_date") or normalized.get("日期") or "",
    )


def apply_interactive_answers(
    sections: list[BidDocumentSection],
    prompts: list[dict[str, Any]],
    answers: dict[str, Any],
) -> list[BidDocumentSection]:
    """将交互式答案回填到章节中。"""
    resolved_answers: dict[str, str] = {}
    for prompt in prompts:
        answer = _answer_lookup(prompt, answers)
        if answer:
            resolved_answers[str(prompt["field_key"])] = answer

    updated_sections: list[BidDocumentSection] = []
    for section in sections:
        content = section.content or ""
        for prompt in prompts:
            answer = resolved_answers.get(str(prompt["field_key"]))
            if not answer:
                continue
            for token in prompt.get("placeholder_tokens", []):
                content = content.replace(str(token), answer)
        updated_sections.append(section.model_copy(update={"content": content}))

    return updated_sections
