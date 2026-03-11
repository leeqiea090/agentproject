"""证据绑定与产品 Profile 构建模块"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.schemas import (
    BidEvidenceBinding,
    DocumentBlock,
    DocumentMode,
    DraftLevel,
    NormalizedRequirement,
    ProcurementPackage,
    ProductProfile,
    TenderDocument,
    TenderSourceBinding,
    ValidationGate,
)
from app.services.requirement_processor import (
    _extract_match_tokens,
    _find_evidence_position,
    _find_requirement_pair_position,
    _markdown_cell,
    _TECH_EXIT_HINTS,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Phase 5: 文档目标判定 — 单包实装 vs 多包底稿
# ═══════════════════════════════════════════════════════════════════

def _determine_document_mode(
    tender: TenderDocument,
    selected_packages: list[str] | None = None,
    mode_hint: str = "",
) -> DocumentMode:
    """判定文档生成模式。

    规则：
    - mode_hint='rich_draft' 且单包 → single_package_rich_draft（必须生成技术表/配置表/服务表/资料表）
    - 只选了 1 个包（或标书只有 1 个包）→ single_package_deep_draft（允许待补，但不允许缺槽位）
    - 否则 → multi_package_master_draft
    """
    is_single = (selected_packages and len(selected_packages) == 1) or len(tender.packages) == 1
    if is_single and mode_hint == "rich_draft":
        return DocumentMode.single_package_rich_draft
    if is_single:
        return DocumentMode.single_package_deep_draft
    return DocumentMode.multi_package_master_draft


def _filter_packages_for_mode(
    tender: TenderDocument,
    mode: DocumentMode,
    target_package_id: str = "",
) -> list[ProcurementPackage]:
    """根据文档模式过滤采购包。"""
    if mode in (
        DocumentMode.single_package,
        DocumentMode.single_package_deep_draft,
        DocumentMode.single_package_rich_draft,
    ) and target_package_id:
        return [p for p in tender.packages if p.package_id == target_package_id]
    return tender.packages


# ═══════════════════════════════════════════════════════════════════
#  Phase 6: 双层证据绑定
# ═══════════════════════════════════════════════════════════════════

def build_tender_source_bindings(
    package_id: str,
    requirements: list[NormalizedRequirement],
    tender_raw: str,
) -> list[TenderSourceBinding]:
    """A层：从招标原文中定位每条需求的来源位置。

    修复：excerpt 不再带出后续多条，精确截断到当前条目句尾。
    """
    bindings: list[TenderSourceBinding] = []
    for req in requirements:
        if req.package_id != package_id:
            continue
        excerpt = ""
        char_start = 0
        char_end = 0
        # 在原文中搜索参数名
        search_key = req.param_name
        pos = tender_raw.find(search_key)
        if pos >= 0:
            char_start = max(0, pos - 20)
            # 从 pos 向后找到当前句子结尾（句号/分号/换行），避免串到下一条
            raw_end = pos + len(search_key) + 100
            raw_end = min(len(tender_raw), raw_end)
            candidate = tender_raw[pos + len(search_key):raw_end]
            # 找最近的句子边界
            for sep in ("\n", "。", "；", ";"):
                sep_pos = candidate.find(sep)
                if sep_pos >= 0 and sep_pos < 80:
                    raw_end = pos + len(search_key) + sep_pos + 1
                    break
            char_end = min(len(tender_raw), raw_end)
            excerpt = tender_raw[char_start:char_end].replace("\n", " ").strip()
            # 去除尾部多余内容
            excerpt = re.sub(r"\s+", " ", excerpt).strip()

        bindings.append(TenderSourceBinding(
            package_id=package_id,
            requirement_id=req.requirement_id,
            source_page=req.source_page,
            source_section="",
            source_excerpt=excerpt,
            char_start=char_start,
            char_end=char_end,
        ))
    return bindings


def enrich_bindings_from_blocks(
    bindings: list[TenderSourceBinding],
    doc_blocks: list[DocumentBlock],
) -> list[TenderSourceBinding]:
    """用 DocumentBlock 的精确元数据补充 TenderSourceBinding 的 page / section。

    对每条 binding，在 doc_blocks 中找到 char_start 落入的块，
    用块的 page / section_title 覆盖原先基于字符偏移量的粗估值。
    """
    if not doc_blocks:
        return bindings

    sorted_blocks = sorted(
        (b for b in doc_blocks if not b.is_noise),
        key=lambda b: b.char_start,
    )
    if not sorted_blocks:
        return bindings

    updated: list[TenderSourceBinding] = []
    for bind in bindings:
        if bind.char_start <= 0:
            updated.append(bind)
            continue
        matched_block: DocumentBlock | None = None
        for blk in sorted_blocks:
            if blk.char_start <= bind.char_start <= blk.char_end:
                matched_block = blk
                break
            if blk.char_start > bind.char_start:
                break
        if matched_block is None:
            # 回退：找最近的前驱块
            for blk in reversed(sorted_blocks):
                if blk.char_start <= bind.char_start:
                    matched_block = blk
                    break
        if matched_block:
            bind = bind.model_copy(update={
                "source_page": matched_block.page or bind.source_page,
                "source_section": matched_block.section_title or bind.source_section,
            })
        updated.append(bind)
    return updated


def build_bid_evidence_bindings(
    package_id: str,
    requirements: list[NormalizedRequirement],
    product: Any = None,
) -> list[BidEvidenceBinding]:
    """B层：从投标材料中定位每条需求的证据来源。"""
    bindings: list[BidEvidenceBinding] = []
    if not product:
        # 无产品信息时，所有需求无证据
        for req in requirements:
            if req.package_id != package_id:
                continue
            bindings.append(BidEvidenceBinding(
                package_id=package_id,
                requirement_id=req.requirement_id,
                evidence_type="",
                file_name="",
                file_page=0,
                snippet="",
                evidence_file="",
                evidence_page=0,
                evidence_snippet="",
                covers_requirement=False,
            ))
        return bindings

    bid_materials = getattr(product, "bid_materials", []) or []
    specs = getattr(product, "specifications", {}) or {}
    evidence_refs = getattr(product, "evidence_refs", []) or []

    for req in requirements:
        if req.package_id != package_id:
            continue

        # 尝试从 evidence_refs 中找到匹配
        best_binding = BidEvidenceBinding(
            package_id=package_id,
            requirement_id=req.requirement_id,
        )

        # 1) 从已有 evidence_refs 匹配
        for ref in evidence_refs:
            ref_param = str(ref.get("param_name", ref.get("parameter", "")))
            if req.param_name and req.param_name in ref_param:
                best_binding = BidEvidenceBinding(
                    package_id=package_id,
                    requirement_id=req.requirement_id,
                    evidence_type=ref.get("evidence_type", "spec_sheet"),
                    file_name=ref.get("file_name", ""),
                    file_page=int(ref.get("page", 0)),
                    snippet=str(ref.get("snippet", "")),
                    evidence_file=ref.get("file_name", ""),
                    evidence_page=int(ref.get("page", 0)),
                    evidence_snippet=str(ref.get("snippet", "")),
                    covers_requirement=True,
                )
                break

        # 2) 从 specifications 匹配
        if not best_binding.covers_requirement and req.param_name in specs:
            best_binding.snippet = str(specs[req.param_name])
            best_binding.evidence_snippet = best_binding.snippet
            best_binding.evidence_type = "spec_sheet"
            best_binding.covers_requirement = True

        # 3) 从 bid_materials 关键页匹配
        if not best_binding.covers_requirement:
            for mat in bid_materials:
                mat_text = getattr(mat, "extracted_text", "") or ""
                if req.param_name and req.param_name in mat_text:
                    best_binding.evidence_type = getattr(mat, "file_type", "brochure")
                    best_binding.file_name = getattr(mat, "file_name", "")
                    best_binding.evidence_file = best_binding.file_name
                    key_pages = getattr(mat, "key_pages", []) or []
                    if key_pages:
                        best_binding.file_page = int(key_pages[0].get("page", 0))
                        best_binding.evidence_page = best_binding.file_page
                    best_binding.snippet = req.param_name
                    best_binding.evidence_snippet = best_binding.snippet
                    best_binding.covers_requirement = True
                    break

        bindings.append(best_binding)
    return bindings


def _compute_evidence_coverage(bindings: list[BidEvidenceBinding]) -> float:
    """计算投标侧证据覆盖率。"""
    if not bindings:
        return 0.0
    covered = sum(1 for b in bindings if b.covers_requirement)
    return covered / len(bindings)


def _determine_draft_level(
    gate: ValidationGate,
    mode: DocumentMode,
) -> DraftLevel:
    """根据校验门和模式判定稿件等级。

    - 多包模式 → 永远内部稿
    - 多包母版底稿 → 永远内部稿
    - 单包深写/富底稿 → 看校验门
    """
    if mode in (DocumentMode.multi_package_draft, DocumentMode.multi_package_master_draft):
        return DraftLevel.internal_draft
    if not gate.passes_external_gate():
        return DraftLevel.internal_draft
    return DraftLevel.external_ready


# ═══════════════════════════════════════════════════════════════════
#  Phase 7: 构建 ProductProfile（writer 输入）
# ═══════════════════════════════════════════════════════════════════

def build_product_profile_for_package(
    package_id: str,
    product: Any = None,
    evidence_bindings: list[BidEvidenceBinding] | None = None,
) -> ProductProfile:
    """为指定包构建 ProductProfile — writer 的核心输入。"""
    if not product:
        return ProductProfile(
            package_id=package_id,
            ready_for_external=False,
        )

    specs = getattr(product, "specifications", {}) or {}
    brand = getattr(product, "brand", "") or ""
    model_name = getattr(product, "model", "") or ""
    manufacturer = getattr(product, "manufacturer", "") or ""
    origin = getattr(product, "origin", "") or ""
    product_name = getattr(product, "product_name", "") or ""
    config_items = getattr(product, "config_items", []) or []
    functional_notes = getattr(product, "functional_notes", "") or ""
    acceptance_notes = getattr(product, "acceptance_notes", "") or ""
    training_notes = getattr(product, "training_notes", "") or ""

    has_identity = bool(brand and model_name and manufacturer)
    has_specs = len(specs) >= 3

    # 需要身份信息 + 技术参数 + 至少部分证据才能外发
    evidence_coverage = _compute_evidence_coverage(evidence_bindings or [])
    ready = has_identity and has_specs and evidence_coverage >= 0.5

    return ProductProfile(
        package_id=package_id,
        product_name=product_name,
        brand=brand,
        model=model_name,
        manufacturer=manufacturer,
        origin=origin,
        specifications=specs,
        config_items=config_items,
        functional_notes=functional_notes,
        acceptance_notes=acceptance_notes,
        training_notes=training_notes,
        has_complete_identity=has_identity,
        has_technical_specs=has_specs,
        ready_for_external=ready,
        evidence_refs=evidence_bindings or [],
    )


# ── Evidence snippet helpers ──

def _trim_evidence_snippet(snippet: str, anchor: str) -> str:
    """清洗原文证据片段，去除前后垃圾文本。

    增强规则：
    1. 按 anchor 定位起始位置
    2. 按退出关键词截断尾部
    3. 按中文句号/分号截断到完整句子
    4. 去除前缀编号、标记符号
    5. 限制最大长度避免过长片段
    """
    normalized = snippet
    if anchor:
        anchor_pos = normalized.find(anchor)
        if anchor_pos >= 0:
            normalized = normalized[anchor_pos:]

    cut_positions: list[int] = []
    for marker in _TECH_EXIT_HINTS:
        pos = normalized.find(marker)
        if pos > max(8, len(anchor)):
            cut_positions.append(pos)
    if cut_positions:
        normalized = normalized[:min(cut_positions)]

    # 清洗：去除前缀编号和标记
    normalized = re.sub(r"^[\s★▲■●\d.、（()）)]+", "", normalized)

    # 按中文句号截断到完整句子（如果太长）
    _MAX_SNIPPET_CHARS = 200
    if len(normalized) > _MAX_SNIPPET_CHARS:
        # 尝试在最后一个句号/分号处截断
        for sep in ("。", "；", ";", "，"):
            last_sep = normalized.rfind(sep, 0, _MAX_SNIPPET_CHARS)
            if last_sep > 20:
                normalized = normalized[:last_sep + 1]
                break
        else:
            normalized = normalized[:_MAX_SNIPPET_CHARS] + "…"

    # 去除尾部非技术内容噪音
    _SNIPPET_TAIL_NOISE = ("详见", "见附件", "按规定", "注：", "备注", "说明：")
    for noise in _SNIPPET_TAIL_NOISE:
        noise_pos = normalized.rfind(noise)
        if noise_pos > 20 and noise_pos > len(normalized) * 0.7:
            normalized = normalized[:noise_pos]

    normalized = normalized.strip(" ；;，,。/\n\r\t")

    # 清洗嵌套占位符文本
    from app.services.quality_gate import _flatten_nested_placeholders
    normalized = _flatten_nested_placeholders(normalized)

    return normalized


def _extract_evidence_snippet(package_raw: str, req_key: str, req_val: str, fallback_raw: str = "") -> tuple[str, str, bool]:
    source = "招标原文片段"
    if not package_raw.strip() and not fallback_raw.strip():
        quote = f"{_markdown_cell(req_key)}：{_markdown_cell(req_val)}（依据结构化解析结果）"
        return source, quote, False

    idx = -1
    matched = ""
    text = ""
    if package_raw.strip():
        idx, matched = _find_requirement_pair_position(package_raw, req_key, req_val)
        if idx >= 0:
            text = package_raw
        else:
            relaxed_candidates = [req_key, *_extract_match_tokens(req_key, req_val)[:6]]
            idx, matched = _find_evidence_position(package_raw, relaxed_candidates)
            if idx >= 0:
                text = package_raw

    if idx < 0 and fallback_raw.strip() and fallback_raw != package_raw:
        idx, matched = _find_requirement_pair_position(fallback_raw, req_key, req_val)
        if idx >= 0:
            text = fallback_raw

    if idx < 0:
        quote = f"{_markdown_cell(req_key)}：{_markdown_cell(req_val)}（原文未定位到完全同名片段）"
        return source, quote, False

    start = max(0, idx - 24)
    end = min(len(text), idx + max(24, len(matched)) + 36)
    snippet = text[start:end].replace("\n", " ").replace("\r", " ")
    snippet = re.sub(r"\s+", " ", snippet).strip()
    snippet = _trim_evidence_snippet(snippet, matched)
    if len(snippet) > 120:
        snippet = snippet[:120] + "..."
    return source, _markdown_cell(snippet), True
