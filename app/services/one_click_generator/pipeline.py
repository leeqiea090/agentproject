from __future__ import annotations

import logging
import re
from typing import Any

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.sections as _sections
import app.services.one_click_generator.writer_contexts as _writer_contexts
import app.services.evidence_binder as _evidence_binder
import app.services.quality_gate as _quality_gate
import app.services.requirement_processor as _requirement_processor
from langchain_openai import ChatOpenAI

from app.schemas import (
    BidEvidenceBinding,
    BidGenerationResult,
    ClauseCategory,
    DocumentMode,
    DraftLevel,
    NormalizedRequirement,
    ProductProfile,
    TenderDocument,
    TenderSourceBinding,
)
from app.services.chunking import split_to_blocks
from app.services.evidence_binder import (
    _compute_evidence_coverage,
    _determine_document_mode,
    _determine_draft_level,
    _filter_packages_for_mode,
    build_bid_evidence_bindings,
    build_product_profile_for_package,
    build_tender_source_bindings,
    enrich_bindings_from_blocks,
)
from app.services.one_click_generator.format_driven_sections import (
    build_format_driven_sections,
)
from app.services.one_click_generator.technical_sections import (
    _generate_rich_draft_sections,
)
from app.services.one_click_generator.writer_contexts import build_writer_contexts

from app.services.quality_gate import (
    _apply_template_pollution_guard,
    _heal_package_contamination,
    _heal_table_mixing,
    annotate_draft_level,
    check_external_content_density,
    compute_regression_metrics,
    compute_validation_gate,
    normalize_pending_draft_sections,
    strip_placeholders_for_external,
)
from app.services.requirement_processor import (
    _atomize_requirements,
    _effective_requirements,
    _extract_package_scope_text,
    filter_requirements_by_category,
    normalize_requirements_to_objects,
)


logger = logging.getLogger(__name__)

def _has_structural_failures(gate) -> bool:
    """仍然属于结构性失败的问题。"""
    return (
        gate.package_contamination_detected
        or gate.table_category_mixing
        or gate.snippet_truncation_count > gate.snippet_truncation_threshold
        or gate.anchor_pollution_rate > gate.anchor_pollution_threshold
        or gate.nested_placeholder_detected
    )


def _pack_normalized_result(all_normalized: dict[str, list[NormalizedRequirement]]) -> dict[str, Any]:
    """打包归一化结果。"""
    def _looks_semantically_thin(value: str) -> bool:
        """判断semanticallythin。"""
        text = str(value or "").strip()
        if not text:
            return True
        if len(text) <= 3:
            return True
        if re.fullmatch(r"[\d.,]+(?:\s*[%A-Za-z/\-._\u00b0\u03bc\u4e00-\u9fff]{0,6})?", text):
            return True
        return False

    def _extract_value(text: str, param_name: str) -> str:
        """提取值。"""
        normalized = str(text or "").strip()
        if not normalized:
            return ""
        if param_name:
            for sep in ("：", ":"):
                prefix = f"{param_name}{sep}"
                if prefix in normalized:
                    value = normalized.split(prefix, 1)[1].strip()
                    if value:
                        return value
        parts = re.split(r"[：:]", normalized, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()
        return normalized

    def _best_normalized_value(req: NormalizedRequirement) -> str:
        """返回归一化值。"""
        raw_text = str(req.raw_text or "").strip()
        source_text = str(req.source_text or "").strip()
        param_name = str(req.param_name or "").strip()

        for candidate_text in (raw_text, source_text):
            candidate = _extract_value(candidate_text, param_name)
            if candidate and not _looks_semantically_thin(candidate):
                return candidate
        for candidate_text in (raw_text, source_text):
            candidate = _extract_value(candidate_text, param_name)
            if candidate:
                return candidate
        if req.operator and req.threshold:
            return f"{req.operator}{req.threshold}{req.unit or ''}"
        if req.threshold:
            return f"{req.threshold}{req.unit or ''}"
        return source_text

    items: list[dict[str, Any]] = []
    for pkg_id, reqs in all_normalized.items():
        for req in reqs:
            # 下游大量按字符串比较 category；这里统一输出 JSON 友好的纯值，避免 Enum 文本泄漏。
            row = req.model_dump(mode="json")
            row["normalized_value"] = _best_normalized_value(req)
            items.append(row)
    return {"technical_requirements": items}


def _pack_product_profiles(all_profiles: dict[str, ProductProfile]) -> dict[str, Any]:
    """打包产品画像。"""
    return {pkg_id: profile.model_dump() for pkg_id, profile in all_profiles.items()}


def _downgrade_to_pending_draft(sections):
    """把当前产物尽量整理成可人工补改的待补充底稿，而不是直接抛错。"""
    downgraded = _apply_template_pollution_guard(sections)
    downgraded = normalize_pending_draft_sections(downgraded)
    downgraded = _apply_template_pollution_guard(downgraded)
    return downgraded


def _infer_material_stage(
    products: dict[str, Any] | None,
    profiles: dict[str, ProductProfile],
) -> tuple[str, list[str]]:
    """
    判定当前生成所处资料阶段：
    - tender_only: 只有招标文件，没有投标产品事实/证据
    - product_only: 已有部分品牌型号/参数，但还没有投标侧证据文件
    - evidence_ready: 已有投标侧证据文件，可尝试 external gate
    """
    products = products or {}

    if not products:
        return "tender_only", ["未提供任何投标产品资料"]

    has_identity = False
    has_specs = False
    has_bid_evidence = False

    for pkg_id, profile in profiles.items():
        if getattr(profile, "has_complete_identity", False):
            has_identity = True
        if getattr(profile, "has_technical_specs", False):
            has_specs = True

        product = products.get(pkg_id)
        if not product:
            continue

        bid_materials = getattr(product, "bid_materials", []) or []
        evidence_refs = getattr(product, "evidence_refs", []) or []
        specifications = getattr(product, "specifications", {}) or {}

        if bid_materials or evidence_refs:
            has_bid_evidence = True

        if len(specifications) >= 3:
            has_specs = True

    if has_bid_evidence:
        return "evidence_ready", []

    if has_identity or has_specs:
        return "product_only", ["已提取到部分品牌/型号/参数，但尚未上传投标侧证据文件"]

    return "tender_only", ["仅接入招标文件，尚无可核验的投标产品事实/证据"]


def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _sections, _writer_contexts, _evidence_binder, _quality_gate, _requirement_processor,):
    __reexport_all(_module)

del _module
def generate_bid_sections(
    tender: TenderDocument,
    tender_raw: str,
    llm: ChatOpenAI,
    products: dict | None = None,
    mode: str = "rich_draft",  # "internal" | "rich_draft"
    *,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, dict[str, Any]] | None = None,
    required_materials: list[str] | None = None,
    selected_packages: list[str] | None = None,
    require_validation_pass: bool = False,
) -> BidGenerationResult:
    """
    根据招标文件生成全部投标文件章节 — 增强版 10 层管道。

    新增能力：
    1. 文档模式判定（单包/多包）
    2. 7 类条款分类
    3. 归一化需求（NormalizedRequirement）
    4. 双层证据绑定（招标侧 + 投标侧）
    5. ProductProfile 构建
    6. 包件隔离硬规则
    7. 4 项硬校验门
    8. 双输出分层（internal_draft / external_ready）
    9. 7 项回归指标
    """
    logger.info("开始一键生成投标文件章节 - 模式: %s", mode)
    logger.debug("招标原文长度：%d 字符", len(tender_raw))
    products = products or {}
    material_stage = "tender_only"
    material_notes: list[str] = []
    external_gate_enabled = False

    # ── Step 0a: 文档接入 — 可引用块 ──
    doc_blocks = split_to_blocks(tender_raw)
    logger.info("文档接入: 生成 %d 个可引用块", len(doc_blocks))

    # ── Step 0: 文档模式判定 ──
    doc_mode = _determine_document_mode(tender, selected_packages, mode_hint=mode)
    logger.info("文档模式: %s", doc_mode.value)

    target_package_ids = selected_packages or [p.package_id for p in tender.packages]

    # 单包模式：仅处理目标包
    if doc_mode in (DocumentMode.single_package, DocumentMode.single_package_deep_draft, DocumentMode.single_package_rich_draft) and target_package_ids:
        active_packages = _filter_packages_for_mode(tender, doc_mode, target_package_ids[0])
    else:
        active_packages = tender.packages

    # ── Step 1: 归一化需求（per-package） ──
    all_normalized: dict[str, list[NormalizedRequirement]] = {}
    # 构建各包产品名列表，用于跨包检测
    all_item_names = {p.package_id: p.item_name for p in tender.packages}
    for pkg in active_packages:
        other_names = [name for pid, name in all_item_names.items() if pid != pkg.package_id]
        raw_reqs = _effective_requirements(pkg, tender_raw)
        atomized = _atomize_requirements(raw_reqs)
        norm_reqs = normalize_requirements_to_objects(
            pkg.package_id, atomized,
            other_package_item_names=other_names,
            package_item_name=pkg.item_name,
            doc_blocks=doc_blocks,
        )
        # 过滤掉噪音条款和跨包条款，不进入主表
        noise_count = sum(1 for r in norm_reqs if r.category == ClauseCategory.noise)
        if noise_count:
            logger.info("包%s 过滤 %d 条跨包噪音/无效条款", pkg.package_id, noise_count)
        norm_reqs = [r for r in norm_reqs if r.category != ClauseCategory.noise]
        all_normalized[pkg.package_id] = norm_reqs
        logger.info(
            "包%s 归一化需求: %d 条 (技术=%d, 配置=%d, 服务=%d, 商务=%d)",
            pkg.package_id,
            len(norm_reqs),
            len(filter_requirements_by_category(norm_reqs, ClauseCategory.technical_requirement)),
            len(filter_requirements_by_category(norm_reqs, ClauseCategory.config_requirement)),
            len(filter_requirements_by_category(norm_reqs, ClauseCategory.service_requirement)),
            len(filter_requirements_by_category(norm_reqs, ClauseCategory.commercial_requirement)),
        )

    # ── Step 2: 双层证据绑定（per-package） ──
    all_tender_bindings: dict[str, list[TenderSourceBinding]] = {}
    all_bid_bindings: dict[str, list[BidEvidenceBinding]] = {}
    all_profiles: dict[str, ProductProfile] = {}

    for pkg in active_packages:
        pkg_reqs = all_normalized.get(pkg.package_id, [])
        product = products.get(pkg.package_id)

        # 获取本包的范围文本，避免跨包搜索
        other_names = tuple(
            p.item_name for p in active_packages if p.package_id != pkg.package_id
        )
        pkg_scope_text = _extract_package_scope_text(pkg, tender_raw, other_names)

        # A层：招标侧溯源（优先在包范围内搜索）
        tender_bindings = build_tender_source_bindings(
            pkg.package_id, pkg_reqs, tender_raw,
            package_scoped_text=pkg_scope_text,
            doc_blocks=doc_blocks,
        )
        # 用文档块的精确页码/章节覆盖粗估值
        tender_bindings = enrich_bindings_from_blocks(tender_bindings, doc_blocks)
        all_tender_bindings[pkg.package_id] = tender_bindings

        # B层：投标侧证据
        bid_bindings = build_bid_evidence_bindings(pkg.package_id, pkg_reqs, product)
        all_bid_bindings[pkg.package_id] = bid_bindings

        evidence_cov = _compute_evidence_coverage(bid_bindings)
        logger.info("包%s 投标侧证据覆盖率: %.1f%%", pkg.package_id, evidence_cov * 100)

        # 构建 ProductProfile
        profile = build_product_profile_for_package(pkg.package_id, product, bid_bindings)
        all_profiles[pkg.package_id] = profile

    material_stage, material_notes = _infer_material_stage(products, all_profiles)
    external_gate_enabled = material_stage == "evidence_ready"
    logger.info(
        "资料阶段: %s%s",
        material_stage,
        f"（{'；'.join(material_notes)}）" if material_notes else "",
    )

    # ── Step 3: 包件隔离生成章节 ──
    # 如果调用方已经给了结构化结果，这里用“本地推导结果 + 外部结果覆写”的方式合并，保证接口兼容。
    normalized_payload = _pack_normalized_result(all_normalized)
    if isinstance(normalized_result, dict):
        normalized_payload = {**normalized_payload, **normalized_result}
        if normalized_result.get("technical_requirements"):
            normalized_payload["technical_requirements"] = normalized_result["technical_requirements"]

    profile_payload = _pack_product_profiles(all_profiles)
    if isinstance(product_profiles, dict):
        profile_payload.update(product_profiles)

    sections = build_format_driven_sections(
        tender=tender,
        tender_raw=tender_raw,
        products=products,
        active_packages=active_packages,
        required_materials=required_materials,
        normalized_result=normalized_payload,
        evidence_result=evidence_result,
        product_profiles=profile_payload,
    )

    # Rich draft 仅追加分表，不追加整段“成熟说明”，避免没有证据的固定承诺混入正文。
    if (mode == "rich_draft" or doc_mode == DocumentMode.single_package_rich_draft) and products:
        all_writer_contexts = {}
        for pkg in active_packages:
            pkg_reqs = all_normalized.get(pkg.package_id, [])
            profile = all_profiles.get(pkg.package_id)
            wctxs = build_writer_contexts(
                package_id=pkg.package_id,
                requirements=pkg_reqs,
                product_profile=profile,
                tender_source_bindings=all_tender_bindings.get(pkg.package_id, []),
                bid_evidence_bindings=all_bid_bindings.get(pkg.package_id, []),
                document_mode=doc_mode,
            )
            all_writer_contexts[pkg.package_id] = wctxs

        rich_sections = _generate_rich_draft_sections(
            tender,
            products,
            normalized_reqs=all_normalized,
            active_packages=active_packages,
            writer_contexts=all_writer_contexts,
        )
        sections.extend(
            section for section in rich_sections
            if section.section_title.endswith("分表响应")
        )

    sections = _apply_template_pollution_guard(sections)

    # ── Step 4: 硬校验 + 自愈循环 ──
    #    生成 → 校验 → 发现问题立即修复 → 重新校验
    #    直到通过；若已无可推进的修复动作，则阻断输出而非降级放行
    _MAX_HEAL_PASSES = 5
    gate = None
    heal_pass = 0
    while heal_pass <= _MAX_HEAL_PASSES:
        gate = compute_validation_gate(
            sections=sections,
            normalized_reqs=all_normalized,
            evidence_bindings=all_bid_bindings,
            target_package_ids=target_package_ids,
            mode=doc_mode,
            tender=tender,
        )
        reasons = gate.failure_reasons()
        logger.info(
            "硬校验(pass=%d): mixing=%s, contamination=%s, placeholders=%d, "
            "evidence=%.1f%%, reasons=%s",
            heal_pass,
            gate.table_category_mixing,
            gate.package_contamination_detected,
            gate.placeholder_count,
            gate.bid_evidence_coverage * 100,
            reasons or "无",
        )

        if external_gate_enabled and gate.passes_external_gate():
            if heal_pass > 0:
                logger.info("自愈成功: 经 %d 轮修复后硬校验通过", heal_pass)
            break

        if not external_gate_enabled:
            if _has_structural_failures(gate):
                logger.info(
                    "当前资料阶段=%s，仅继续处理结构性问题；占位符/证据问题转为待补充底稿。",
                    material_stage,
                )
            else:
                sections = normalize_pending_draft_sections(sections)
                break

        if heal_pass >= _MAX_HEAL_PASSES:
            if _has_structural_failures(gate):
                logger.warning(
                    "达到最大自愈轮次但仍有结构问题，降级输出待补充底稿："
                    "contamination=%s, mixing=%s, truncation=%d, "
                    "anchor_pollution=%.2f, nested_placeholders=%s",
                    gate.package_contamination_detected,
                    gate.table_category_mixing,
                    gate.snippet_truncation_count,
                    gate.anchor_pollution_rate,
                    gate.nested_placeholder_detected,
                )
                sections = _downgrade_to_pending_draft(sections)
                break

            # 非结构型问题（如证据不足、占位较多）也统一降级，不再抛 500
            sections = _downgrade_to_pending_draft(sections)
            break

        # ── 自愈动作 ──
        logger.info("自愈 pass %d: 修复 %s", heal_pass + 1, reasons)
        before_snapshot = tuple((s.section_title, s.content) for s in sections)

        # 修复1: 表格混装 → 从技术表移除非技术行
        if gate.table_category_mixing:
            sections = _heal_table_mixing(sections)

        # 修复2: 包件污染 → 重新过滤（已在生成阶段做过包隔离，这里做文本级兜底）
        if gate.package_contamination_detected and target_package_ids:
            sections = _heal_package_contamination(sections, target_package_ids)

        # 修复3: 模板污染/锚点污染 → 清理提示词与未渲染标记
        sections = _apply_template_pollution_guard(sections)

        # 缺少上游真值时，统一转成“待补充”底稿而不是保留脏占位符。
        if (
            gate.placeholder_count > gate.placeholder_threshold
            or gate.bid_evidence_coverage < gate.evidence_coverage_threshold
            or gate.evidence_blank_rate > gate.evidence_blank_threshold
        ):
            sections = normalize_pending_draft_sections(sections)

        after_snapshot = tuple((s.section_title, s.content) for s in sections)
        heal_pass += 1
        if after_snapshot == before_snapshot:
            if _has_structural_failures(gate):
                logger.warning(
                    "自愈 pass %d 无进展但仍有结构问题，降级输出待补充底稿。问题: %s",
                    heal_pass,
                    "；".join(reasons) or "无",
                )
                sections = _downgrade_to_pending_draft(sections)
                break

            if not gate.passes_external_gate():
                sections = _downgrade_to_pending_draft(sections)

            logger.info(
                "自愈 pass %d 无新增修复动作，%s。问题: %s",
                heal_pass,
                "已转为待补充底稿" if not gate.passes_external_gate() else "校验已通过",
                "；".join(reasons) or "无",
            )
            break

    # ── 重新计算最终 gate，确保与最终 sections 一致 ──
    gate = compute_validation_gate(
        sections=sections,
        normalized_reqs=all_normalized,
        evidence_bindings=all_bid_bindings,
        target_package_ids=target_package_ids,
        tender=tender,
    )
    reasons = gate.failure_reasons()

    display_reasons = reasons
    if not external_gate_enabled:
        display_reasons = [
            r for r in reasons
            if not (r.startswith("证据覆盖不足") or r.startswith("证据空白"))
        ]
        if not display_reasons and reasons:
            display_reasons = ["投标侧资料未齐，当前输出为待补充底稿"]

    logger.info(
        "硬校验(pass=%d): mixing=%s, contamination=%s, placeholders=%d, "
        "evidence=%.1f%%, reasons=%s",
        heal_pass,
        gate.table_category_mixing,
        gate.package_contamination_detected,
        gate.placeholder_count,
        gate.bid_evidence_coverage * 100,
        display_reasons or "无",
    )

    # ── Step 5: 稿件等级判定 & 双输出 ──
    if external_gate_enabled:
        draft_level = _determine_draft_level(gate, doc_mode)
    else:
        draft_level = DraftLevel.internal_draft
    logger.info("稿件等级: %s", draft_level.value)

    if draft_level == DraftLevel.internal_draft:
        sections = normalize_pending_draft_sections(sections)
        sections = annotate_draft_level(sections, draft_level)
    elif draft_level == DraftLevel.external_ready:
        sections = strip_placeholders_for_external(sections)
        # 检查外发稿实际内容密度，如果过多 "详见..." 引用则降级为 internal
        content_density = check_external_content_density(sections)
        if content_density < 0.5:
            logger.warning(
                "外发稿实际内容密度不足 (%.1f%% < 50%%)，降级为 internal_draft",
                content_density * 100,
            )
            draft_level = DraftLevel.internal_draft
            sections = annotate_draft_level(sections, draft_level)

    if external_gate_enabled and not gate.passes_external_gate() and mode == "rich_draft":
        logger.warning(
            "外发稿硬校验未通过，已阻断对外输出。原因: contamination=%s, placeholders=%d, "
            "evidence=%.1f%%, mixing=%s",
            gate.package_contamination_detected,
            gate.placeholder_count,
            gate.bid_evidence_coverage * 100,
            gate.table_category_mixing,
        )
    elif (not external_gate_enabled) and mode == "rich_draft":
        logger.info(
            "本次仅输出 internal_draft，未启动 external gate。原因: %s",
            "；".join(material_notes) or "投标侧资料未齐",
        )

    # ── Step 6: 回归指标 ──
    metrics = compute_regression_metrics(
        sections=sections,
        normalized_reqs=all_normalized,
        evidence_bindings=all_bid_bindings,
        target_package_ids=target_package_ids,
        tender=tender,
        workflow_stage=material_stage,
    )
    logger.info(
        "回归指标: focus=%.2f, contamination=%.2f, mixing=%.2f, evidence=%.2f, "
        "placeholders=%.2f, config=%.2f, density=%.1f, snippet_clean=%.2f, usability=%.2f, project_meta=%.2f",
        metrics.single_package_focus_score,
        metrics.package_contamination_rate,
        metrics.table_category_mixing_rate,
        metrics.bid_evidence_coverage,
        metrics.placeholder_leakage,
        metrics.config_detail_score,
        metrics.fact_density_per_page,
        metrics.snippet_cleanliness_score,
        metrics.draft_usability_score,
        metrics.project_meta_consistency_score,
    )

    logger.info("一键投标文件章节生成完成，共 %d 章", len(sections))
    return BidGenerationResult(
        sections=sections,
        validation_gate=gate,
        regression_metrics=metrics,
        draft_level=draft_level,
        document_mode=doc_mode,
        product_profiles=profile_payload,
    )
