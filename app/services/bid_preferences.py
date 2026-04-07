"""投标文件生成偏好处理。"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.schemas import (
    BidDocumentSection,
    BidGenerationPreferences,
    BidLanguageStyle,
)

logger = logging.getLogger(__name__)

_TABLE_HEAVY_TITLE_TOKENS = (
    "报价表",
    "报价一览表",
    "分项报价表",
    "审查",
    "响应对照表",
    "偏离表",
    "明细表",
    "汇总表",
)

_STYLE_GUIDANCE = {
    BidLanguageStyle.standard: "保持当前标准招投标书写风格，不做额外修辞扩写。",
    BidLanguageStyle.formal_precise: "使用正式、严谨、规范的政采投标语言，句式完整，措辞克制。",
    BidLanguageStyle.concise_professional: "使用简洁、专业、直接的商务书面语，减少重复套话，但保留必要承诺。",
    BidLanguageStyle.assertive_commitment: "突出执行承诺与交付把控，语气坚定，但不得新增任何未提供的事实或承诺。",
    BidLanguageStyle.explanatory: "适度增加说明性表达，让实施安排、服务措施和响应逻辑更易读。",
}


def _normalize_title(text: str) -> str:
    normalized = re.sub(r"[\s#`*:：、（）()\-—_]", "", text or "")
    normalized = re.sub(r"^第?[一二三四五六七八九十百0123456789]+章", "", normalized)
    normalized = re.sub(r"^附?[一二三四五六七八九十百0123456789]+", "", normalized)
    return normalized


def normalize_generation_preferences(
    preferences: BidGenerationPreferences | dict[str, Any] | None,
) -> BidGenerationPreferences | None:
    """将输入归一化为生成偏好对象。"""
    if preferences is None:
        return None
    if isinstance(preferences, BidGenerationPreferences):
        return preferences
    if isinstance(preferences, dict):
        return BidGenerationPreferences(**preferences)
    raise TypeError(f"Unsupported generation preferences type: {type(preferences)!r}")


def reorder_bid_sections(
    sections: list[BidDocumentSection],
    preferences: BidGenerationPreferences | dict[str, Any] | None,
) -> list[BidDocumentSection]:
    """按用户偏好重排章节顺序。"""
    prefs = normalize_generation_preferences(preferences)
    if prefs is None or not prefs.section_order:
        return list(sections)

    ordered_titles = list(prefs.section_order)

    def _rank_for_title(title: str) -> tuple[int, int]:
        normalized = _normalize_title(title)
        for idx, wanted in enumerate(ordered_titles):
            wanted_normalized = _normalize_title(wanted)
            if not wanted_normalized:
                continue
            if normalized == wanted_normalized:
                return 0, idx
            if normalized.startswith(wanted_normalized) or wanted_normalized.startswith(normalized):
                return 1, idx
        return 2, len(ordered_titles)

    sortable = [
        (section, original_idx, *_rank_for_title(section.section_title))
        for original_idx, section in enumerate(sections)
    ]
    sortable.sort(key=lambda item: (item[2], item[3], item[1]))
    return [section for section, *_ in sortable]


def ordered_section_titles(
    sections: list[BidDocumentSection],
    preferences: BidGenerationPreferences | dict[str, Any] | None,
) -> list[str]:
    """返回按偏好排序后的章节标题。"""
    return [section.section_title for section in reorder_bid_sections(sections, preferences)]


def _llm_message_to_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


def _has_markdown_table(content: str) -> bool:
    return sum(1 for line in content.splitlines() if line.strip().startswith("|")) >= 2


def _placeholder_counter(content: str) -> Counter[str]:
    return Counter(re.findall(r"【待填写：[^】]+】", content or ""))


def _is_safe_rewrite(original: str, rewritten: str) -> bool:
    if not rewritten.strip():
        return False
    original_placeholders = _placeholder_counter(original)
    rewritten_placeholders = _placeholder_counter(rewritten)
    for token, count in original_placeholders.items():
        if rewritten_placeholders[token] < count:
            return False
    if _has_markdown_table(original) and sum(1 for line in rewritten.splitlines() if line.strip().startswith("|")) < sum(
        1 for line in original.splitlines() if line.strip().startswith("|")
    ):
        return False
    return len(rewritten.strip()) >= min(len(original.strip()), max(int(len(original.strip()) * 0.35), 24))


def _should_polish_section(section: BidDocumentSection) -> bool:
    title = str(section.section_title or "").strip()
    content = str(section.content or "").strip()
    if len(content) < 80:
        return False
    if title in {"封面", "目录"}:
        return False
    if any(token in title for token in _TABLE_HEAVY_TITLE_TOKENS):
        return False
    if _has_markdown_table(content):
        return False
    return True


def _style_instruction(preferences: BidGenerationPreferences) -> str:
    base = _STYLE_GUIDANCE.get(preferences.language_style, _STYLE_GUIDANCE[BidLanguageStyle.standard])
    extra = str(preferences.custom_language_instruction or "").strip()
    if not extra:
        return base
    return f"{base}\n补充要求：{extra}"


def _rewrite_section_with_llm(
    section: BidDocumentSection,
    preferences: BidGenerationPreferences,
    llm: Any,
) -> BidDocumentSection:
    system_prompt = (
        "你是招投标文档语言润色器。\n"
        "任务：在不改变事实、数字、承诺边界、品牌型号、表格结构、Markdown 标题层级和占位符的前提下，"
        "把章节内容调整为指定语言风格。\n"
        "禁止新增任何原文没有的资质、页码、参数值、案例、承诺或结论。\n"
        "禁止删除或改写占位符，例如【待填写：投标人名称】必须原样保留。\n"
        "只输出润色后的 Markdown 正文，不要解释。"
    )
    user_prompt = (
        f"章节标题：{section.section_title}\n"
        f"目标风格：{_style_instruction(preferences)}\n\n"
        "原始内容如下：\n"
        f"{section.content}"
    )
    response = llm.invoke([SystemMessage(system_prompt), HumanMessage(user_prompt)])
    rewritten = _llm_message_to_text(response)
    if not _is_safe_rewrite(section.content, rewritten):
        return section
    return section.model_copy(update={"content": rewritten})


def apply_generation_preferences(
    sections: list[BidDocumentSection],
    preferences: BidGenerationPreferences | dict[str, Any] | None,
    *,
    llm: Any | None = None,
    apply_language_style: bool = True,
) -> list[BidDocumentSection]:
    """对章节应用排序和语言风格偏好。"""
    prefs = normalize_generation_preferences(preferences)
    ordered_sections = reorder_bid_sections(sections, prefs)
    if (
        prefs is None
        or not apply_language_style
        or prefs.language_style == BidLanguageStyle.standard
        and not prefs.custom_language_instruction
        or llm is None
        or not hasattr(llm, "invoke")
    ):
        return ordered_sections

    styled_sections: list[BidDocumentSection] = []
    for section in ordered_sections:
        if not _should_polish_section(section):
            styled_sections.append(section)
            continue
        try:
            styled_sections.append(_rewrite_section_with_llm(section, prefs, llm))
        except Exception as exc:  # noqa: BLE001
            logger.warning("章节语言风格调整失败，回退原文: %s", exc)
            styled_sections.append(section)
    return styled_sections
