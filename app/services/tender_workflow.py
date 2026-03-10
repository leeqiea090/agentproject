"""招投标四阶段正式工作流服务。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.schemas import BidDocumentSection, CompanyProfile, ProductSpecification, TenderDocument
from app.services.one_click_generator import generate_bid_sections
from app.services.retriever import search_knowledge

logger = logging.getLogger(__name__)

_MAX_RAW_PROMPT_CHARS = 24000
_MAX_REVIEW_SECTION_CHARS = 1800
_MAX_CITATION_QUOTE_CHARS = 220
_DEFAULT_CITATION_TOP_K = 6
_PLACEHOLDER_PATTERNS = (
    "[待填写]",
    "[投标方公司名称]",
    "[法定代表人]",
    "[授权代表]",
    "[联系电话]",
    "[联系地址]",
    "（此处留空",
    "(此处留空",
)


def _llm_call(llm: ChatOpenAI, system_prompt: str, user_prompt: str) -> str:
    response = llm.invoke([SystemMessage(system_prompt), HumanMessage(user_prompt)])
    content = response.content
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )
    return str(content).strip()


def _extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    return json.loads(raw)


def _ensure_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _to_int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _prepare_citations(hits: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str]] = set()

    for hit in hits:
        if not isinstance(hit, dict):
            continue

        metadata = hit.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        source = str(metadata.get("source") or "unknown").strip() or "unknown"
        chunk_index = _to_int_or_none(metadata.get("chunk_index"))

        score: float | None = None
        raw_score = hit.get("score")
        if raw_score is not None:
            try:
                score = round(float(raw_score), 6)
            except (TypeError, ValueError):
                score = None

        quote = str(hit.get("text") or "").replace("\n", " ").strip()
        if len(quote) > _MAX_CITATION_QUOTE_CHARS:
            quote = quote[:_MAX_CITATION_QUOTE_CHARS] + "..."
        if not quote:
            continue

        dedup_key = (source, chunk_index, quote)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        citations.append(
            {
                "source": source,
                "chunk_index": chunk_index,
                "score": score,
                "quote": quote,
            }
        )

        if len(citations) >= max(1, limit):
            break

    return citations


def _retrieve_citations(query: str, preferred_source: str | None = None, top_k: int = _DEFAULT_CITATION_TOP_K) -> list[dict[str, Any]]:
    if not query.strip():
        return []

    try:
        hits = search_knowledge(query=query, top_k=max(1, top_k))
    except Exception as exc:  # noqa: BLE001
        logger.warning("检索引用失败，query=%s, error=%s", query, exc)
        return []

    if not hits:
        return []

    if preferred_source:
        preferred_hits: list[dict[str, Any]] = []
        for hit in hits:
            metadata = hit.get("metadata", {})
            source = str(metadata.get("source", "")).strip()
            if source == preferred_source:
                preferred_hits.append(hit)
        if preferred_hits:
            hits = preferred_hits

    return _prepare_citations(hits, limit=top_k)


def _material_coverage(required_materials: list[str], sections: list[BidDocumentSection]) -> tuple[int, int, list[str]]:
    if not required_materials:
        return 0, 0, []

    full_text = "\n".join(sec.content for sec in sections)
    full_text = full_text.lower()

    matched = 0
    missing: list[str] = []
    for item in required_materials:
        normalized = item.strip()
        if not normalized:
            continue
        tokens = [tok for tok in re.split(r"[，,、；;（）()\\s/]+", normalized) if len(tok) >= 2]
        if not tokens:
            tokens = [normalized]
        if any(token.lower() in full_text for token in tokens[:4]):
            matched += 1
        else:
            missing.append(normalized)

    total = len([x for x in required_materials if x.strip()])
    return matched, total, missing


def _second_validation(
    analysis_result: dict[str, Any],
    validation_result: dict[str, Any],
    sections: list[BidDocumentSection],
    generation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    check_items: list[dict[str, str]] = []
    issues: list[str] = []
    suggestions: list[str] = []

    validation_status = str(validation_result.get("overall_status", "")).strip()
    material_pass = validation_status == "通过"
    check_items.append(
        {
            "name": "资料完整性复核",
            "status": "通过" if material_pass else "需修订",
            "detail": f"第二步校验状态：{validation_status or '未提供'}",
        }
    )
    if not material_pass:
        issues.append("资料校验未通过，存在缺失项或待确认项。")
        suggestions.append("先完成缺失资料补齐，再重新运行流程。")

    section_titles = [sec.section_title for sec in sections]
    required_chapters = ("第一章", "第二章", "第三章", "第四章")
    missing_chapters = [
        chapter
        for chapter in required_chapters
        if not any(chapter in title for title in section_titles)
    ]
    chapter_pass = not missing_chapters
    chapter_detail = "章节完整" if chapter_pass else f"缺少章节：{', '.join(missing_chapters)}"
    check_items.append(
        {
            "name": "分章节完整性",
            "status": "通过" if chapter_pass else "需修订",
            "detail": chapter_detail,
        }
    )
    if not chapter_pass:
        issues.append(f"分章节生成不完整：{', '.join(missing_chapters)}。")
        suggestions.append("补齐缺失章节，确保投标文件结构完整。")

    placeholder_total = 0
    placeholder_section_details: list[str] = []
    for sec in sections:
        count = 0
        for pattern in _PLACEHOLDER_PATTERNS:
            count += sec.content.count(pattern)
        if count > 0:
            placeholder_total += count
            placeholder_section_details.append(f"{sec.section_title}({count}处)")

    placeholder_pass = placeholder_total == 0
    placeholder_detail = (
        "未发现占位符。"
        if placeholder_pass
        else f"发现 {placeholder_total} 处占位符：{'；'.join(placeholder_section_details)}"
    )
    check_items.append(
        {
            "name": "占位符与留空项检查",
            "status": "通过" if placeholder_pass else "需修订",
            "detail": placeholder_detail,
        }
    )
    if not placeholder_pass:
        issues.append("标书中仍存在未替换占位符或留空说明。")
        suggestions.append("逐章替换 [待填写]/公司信息占位符，并补齐附件留空项。")

    evidence_mapping_pass = any("技术条款证据映射表" in sec.content for sec in sections)
    check_items.append(
        {
            "name": "技术条款证据映射",
            "status": "通过" if evidence_mapping_pass else "需修订",
            "detail": "已检测到技术条款证据映射表" if evidence_mapping_pass else "未检测到“技术条款证据映射表”章节内容",
        }
    )
    if not evidence_mapping_pass:
        issues.append("技术章节缺少证据映射表，参数与原文无法一一追溯。")
        suggestions.append("在第三章补充“技术条款证据映射表”，逐条关联招标原文片段。")

    required_materials = _ensure_str_list(analysis_result.get("required_materials"))
    matched, total, missing = _material_coverage(required_materials, sections)
    coverage_ratio = 1.0 if total == 0 else matched / total
    coverage_pass = coverage_ratio >= 0.6
    check_items.append(
        {
            "name": "资料覆盖率检查",
            "status": "通过" if coverage_pass else "需修订",
            "detail": f"覆盖 {matched}/{total} 项（覆盖率 {coverage_ratio:.0%}）",
        }
    )
    if not coverage_pass and total > 0:
        preview_missing = "；".join(missing[:5]) if missing else "多项资料未覆盖"
        issues.append(f"资料覆盖不足：{preview_missing}。")
        suggestions.append("根据“需准备资料清单”补齐对应章节内容与附件说明。")

    analysis_citations = analysis_result.get("citations")
    if not isinstance(analysis_citations, list):
        analysis_citations = []
    generation_citations: list[dict[str, Any]] = []
    if generation_result and isinstance(generation_result.get("citations"), list):
        generation_citations = generation_result["citations"]

    citation_count = len(analysis_citations) + len(generation_citations)
    citation_pass = citation_count > 0
    check_items.append(
        {
            "name": "检索引用可追溯性",
            "status": "通过" if citation_pass else "需修订",
            "detail": f"可追溯引用条数：{citation_count}",
        }
    )
    if not citation_pass:
        issues.append("未生成检索引用，难以追溯结论依据。")
        suggestions.append("先将招标原文入库并重跑流程，确保输出包含 citations。")

    overall_status = "通过" if not issues else "需修订"
    if not suggestions and overall_status == "通过":
        suggestions = ["可进入人工终审与盖章提交流程。"]

    summary = (
        f"二次校验完成：{len(check_items)} 项，"
        f"{'全部通过' if overall_status == '通过' else f'发现 {len(issues)} 项问题'}。"
    )

    return {
        "executed": True,
        "overall_status": overall_status,
        "check_items": check_items,
        "issues": issues,
        "suggestions": suggestions,
        "summary": summary,
    }


def _append_unique(base: list[str], extras: list[str]) -> list[str]:
    for item in extras:
        normalized = str(item).strip()
        if normalized and normalized not in base:
            base.append(normalized)
    return base


def _format_eval_rules(evaluation_criteria: dict[str, Any]) -> list[str]:
    if not evaluation_criteria:
        return []
    rules: list[str] = []
    for k, v in evaluation_criteria.items():
        if isinstance(v, (int, float)):
            rules.append(f"{k}：{v}")
        else:
            rules.append(f"{k}：{v}")
    return rules


def _default_required_materials(tender: TenderDocument) -> list[str]:
    materials = [
        "营业执照及法定代表人身份证明",
        "法定代表人授权书及授权代表身份证明",
        "供应商资格承诺函（含政府采购法第二十二条相关承诺）",
        "依法缴纳税收和社保证明材料",
        "信用记录查询截图（信用中国/中国政府采购网等）",
        "报价书、报价一览表、报价明细表",
        "技术偏离表及详细配置明细表",
        "技术服务与售后服务方案",
    ]
    joined = " ".join(pkg.item_name for pkg in tender.packages)
    if "医疗器械" in joined or "流式" in joined:
        materials.extend(
            [
                "医疗器械经营许可证/备案凭证（如适用）",
                "产品注册证/备案证明（如适用）",
                "厂家授权书（如适用）",
            ]
        )
    # 去重并保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for item in materials:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def _default_step1_result(tender: TenderDocument) -> dict[str, Any]:
    packages = [
        {
            "package_id": pkg.package_id,
            "item_name": pkg.item_name,
            "quantity": pkg.quantity,
            "budget": pkg.budget,
        }
        for pkg in tender.packages
    ]
    return {
        "key_information": {
            "project_name": tender.project_name,
            "project_number": tender.project_number,
            "purchaser": tender.purchaser,
            "agency": tender.agency,
            "procurement_type": tender.procurement_type,
            "budget": tender.budget,
            "packages": packages,
            "commercial_terms": tender.commercial_terms.model_dump(),
        },
        "required_materials": _default_required_materials(tender),
        "scoring_rules": _format_eval_rules(tender.evaluation_criteria),
        "risk_alerts": [
            "请重点核对投标有效期、交货期限和履约保证金条款。",
            "请确保技术参数响应表逐条对应，不要遗漏关键参数。",
            "证照与授权文件需在有效期内，且与投标产品一致。",
        ],
        "citations": [],
        "summary": "已完成招标关键信息、资料清单和评分规则提取。",
    }


def _material_item(item: str, status: str, evidence: str, suggestion: str = "") -> dict[str, str]:
    return {
        "item": item,
        "status": status,
        "evidence": evidence,
        "suggestion": suggestion,
    }


def _default_step4_if_blocked(reason: str) -> dict[str, Any]:
    return {
        "ready_for_submission": False,
        "risk_level": "high",
        "compliance_score": 0.0,
        "major_issues": [reason],
        "recommendations": ["先补齐资料缺口，再重新运行第三步与第四步。"],
        "secondary_validation": {
            "executed": False,
            "overall_status": "需修订",
            "check_items": [],
            "issues": [reason],
            "suggestions": ["先补齐资料缺口后再执行二次校验。"],
            "summary": "未执行二次校验：缺少可审核标书内容。",
        },
        "conclusion": "当前不具备提交条件。",
    }


class TenderWorkflowAgent:
    """四阶段招投标工作流 Agent。"""

    def __init__(self, llm: ChatOpenAI):
        self.llm = llm

    def step1_analyze_tender(
        self,
        tender: TenderDocument,
        raw_text: str,
        kb_source: str | None = None,
    ) -> dict[str, Any]:
        """第一步：解析招标文件并提炼关键结果。"""
        fallback = _default_step1_result(tender)
        raw_excerpt = (raw_text or "")[:_MAX_RAW_PROMPT_CHARS]
        package_names = "、".join(pkg.item_name for pkg in tender.packages if pkg.item_name.strip())
        citation_query = "；".join(
            part
            for part in [
                tender.project_name.strip(),
                tender.project_number.strip(),
                package_names,
                "评分标准 资格要求 商务条款 资料清单",
            ]
            if part
        )
        citations = _retrieve_citations(
            query=citation_query,
            preferred_source=kb_source,
            top_k=_DEFAULT_CITATION_TOP_K,
        )
        system_prompt = (
            "你是“招标解析Agent”。你的任务是根据招标文件结构化信息和原文，"
            "输出投标准备所需的关键信息。只允许输出JSON。"
        )
        user_prompt = (
            "请输出JSON，结构如下：\n"
            "{\n"
            '  "key_information": { ... },\n'
            '  "required_materials": ["..."],\n'
            '  "scoring_rules": ["..."],\n'
            '  "risk_alerts": ["..."],\n'
            '  "summary": "..."\n'
            "}\n\n"
            "要求：\n"
            "1. key_information需包含项目名称、项目编号、采购人、采购方式、预算、包信息、核心商务条款；\n"
            "2. required_materials必须是可执行的资料清单；\n"
            "3. scoring_rules从评分标准中提炼，若文件无明确权重请说明；\n"
            "4. risk_alerts给出3~6条最关键风险提示。\n\n"
            f"结构化招标信息：\n{json.dumps(tender.model_dump(), ensure_ascii=False)}\n\n"
            f"招标原文（截断）：\n{raw_excerpt}"
        )
        try:
            content = _llm_call(self.llm, system_prompt, user_prompt)
            parsed = _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Step1 解析失败，使用默认结果：%s", exc)
            return fallback

        key_info = parsed.get("key_information")
        required_materials = _ensure_str_list(parsed.get("required_materials"))
        scoring_rules = _ensure_str_list(parsed.get("scoring_rules"))
        risk_alerts = _ensure_str_list(parsed.get("risk_alerts"))
        summary = str(parsed.get("summary", "")).strip()

        if not isinstance(key_info, dict):
            key_info = fallback["key_information"]
        if not required_materials:
            required_materials = fallback["required_materials"]
        if not scoring_rules:
            scoring_rules = fallback["scoring_rules"] or ["评分规则需结合招标文件评审办法人工确认。"]
        if not risk_alerts:
            risk_alerts = fallback["risk_alerts"]
        if not summary:
            summary = fallback["summary"]
        if citations:
            summary = f"{summary}（已附 {len(citations)} 条检索引用）"

        return {
            "key_information": key_info,
            "required_materials": required_materials,
            "scoring_rules": scoring_rules,
            "risk_alerts": risk_alerts,
            "citations": citations,
            "summary": summary,
        }

    def step2_validate_materials(
        self,
        tender: TenderDocument,
        required_materials: list[str],
        selected_packages: list[str],
        company: CompanyProfile | None,
        products: dict[str, ProductSpecification],
    ) -> dict[str, Any]:
        """第二步：按招标要求校验已上传资料。"""
        package_ids = {pkg.package_id for pkg in tender.packages}
        target_packages = selected_packages or sorted(package_ids)
        checklist: list[dict[str, str]] = []

        if company is None:
            checklist.append(
                _material_item("企业基本信息", "缺失", "未提供 company_profile_id", "先上传企业信息再继续。")
            )
        else:
            company_ok = all(
                [
                    bool(company.name.strip()),
                    bool(company.legal_representative.strip()),
                    bool(company.address.strip()),
                    bool(company.phone.strip()),
                ]
            )
            checklist.append(
                _material_item(
                    "企业基本信息",
                    "通过" if company_ok else "缺失",
                    f"企业：{company.name or '-'}，法定代表人：{company.legal_representative or '-'}",
                    "补齐企业名称、法定代表人、地址、电话四项信息。",
                )
            )

            has_licenses = len(company.licenses) > 0
            checklist.append(
                _material_item(
                    "企业证照资料",
                    "通过" if has_licenses else "缺失",
                    f"证照数量：{len(company.licenses)}",
                    "至少补充营业执照及与项目匹配的资质证照。",
                )
            )

            has_staff = len(company.staff) > 0
            checklist.append(
                _material_item(
                    "项目人员资料",
                    "通过" if has_staff else "待确认",
                    f"人员数量：{len(company.staff)}",
                    "建议补充项目负责人及关键岗位人员清单。",
                )
            )

        for pkg_id in target_packages:
            if pkg_id not in package_ids:
                checklist.append(
                    _material_item(
                        f"包{pkg_id} 投标范围",
                        "缺失",
                        "包号不存在于招标文件",
                        "请检查 selected_packages 与招标文件包号是否一致。",
                    )
                )
                continue

            product = products.get(pkg_id)
            if product is None:
                checklist.append(
                    _material_item(
                        f"包{pkg_id} 产品资料",
                        "缺失",
                        "未提供对应产品信息",
                        "请在 product_ids 中补充包号到产品ID的映射，并先创建产品资料。",
                    )
                )
                continue

            product_ok = bool(product.product_name.strip()) and product.price > 0 and bool(product.specifications)
            checklist.append(
                _material_item(
                    f"包{pkg_id} 产品资料",
                    "通过" if product_ok else "缺失",
                    f"产品：{product.product_name}；型号：{product.model or '-'}；参数项：{len(product.specifications)}",
                    "补充产品型号、关键技术参数和价格信息。",
                )
            )

        checklist.append(
            _material_item(
                "招标要求资料覆盖",
                "待确认",
                f"需准备资料项：{len(required_materials)}",
                "对照“需准备资料清单”逐项上传附件。",
            )
        )

        missing_items = [
            item["item"]
            for item in checklist
            if item["status"] == "缺失"
        ]

        system_prompt = (
            "你是“资料校验Agent”。根据已上传资料与招标要求，输出校验结论。"
            "只允许输出JSON。"
        )
        user_prompt = (
            "请输出JSON，结构如下：\n"
            "{\n"
            '  "overall_status": "通过/需补充",\n'
            '  "summary": "...",\n'
            '  "missing_items": ["..."],\n'
            '  "next_actions": ["..."]\n'
            "}\n\n"
            "输入信息：\n"
            f"- 项目：{tender.project_name}\n"
            f"- 目标包号：{target_packages}\n"
            f"- 需准备资料：{required_materials}\n"
            f"- 校验清单：{json.dumps(checklist, ensure_ascii=False)}\n"
            "要求：next_actions 给出可执行动作，最多6条。"
        )

        overall_status = "需补充" if missing_items else "通过"
        summary = "资料校验完成。"
        next_actions = [
            "按缺失项补齐企业资料、产品资料和资质附件。",
            "补充后重新运行第二步校验，确认状态为“通过”。",
        ]

        try:
            content = _llm_call(self.llm, system_prompt, user_prompt)
            parsed = _extract_json(content)
            parsed_missing = _ensure_str_list(parsed.get("missing_items"))
            parsed_actions = _ensure_str_list(parsed.get("next_actions"))
            parsed_status = str(parsed.get("overall_status", "")).strip()
            parsed_summary = str(parsed.get("summary", "")).strip()

            if parsed_status in {"通过", "需补充"}:
                overall_status = parsed_status
            if parsed_summary:
                summary = parsed_summary
            if parsed_actions:
                next_actions = parsed_actions
            if parsed_missing:
                missing_items = sorted(set(missing_items + parsed_missing))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Step2 AI总结失败，使用规则结果：%s", exc)

        if missing_items:
            overall_status = "需补充"

        return {
            "overall_status": overall_status,
            "checklist": checklist,
            "missing_items": missing_items,
            "next_actions": next_actions,
            "summary": summary,
        }

    def step3_integrate_bid(
        self,
        tender: TenderDocument,
        raw_text: str,
        selected_packages: list[str],
        company: CompanyProfile,
        products: dict[str, ProductSpecification],
        kb_source: str | None = None,
    ) -> tuple[dict[str, Any], list[BidDocumentSection]]:
        """第三步：整合生成标书内容。"""
        package_ids = {pkg.package_id for pkg in tender.packages}
        target_packages = selected_packages or sorted(package_ids)
        filtered_packages = [pkg for pkg in tender.packages if pkg.package_id in target_packages]
        if not filtered_packages:
            filtered_packages = tender.packages

        filtered_tender = tender.model_copy(update={"packages": filtered_packages})

        product_summary = []
        for pkg_id in target_packages:
            product = products.get(pkg_id)
            if product:
                product_summary.append(
                    {
                        "package_id": pkg_id,
                        "product_name": product.product_name,
                        "model": product.model,
                        "manufacturer": product.manufacturer,
                    }
                )

        citation_query = "；".join(
            [
                filtered_tender.project_name,
                filtered_tender.project_number,
                " ".join(pkg.item_name for pkg in filtered_packages if pkg.item_name.strip()),
                "技术参数 交货期 投标报价 评分点",
            ]
        ).strip("；")
        citations = _retrieve_citations(
            query=citation_query,
            preferred_source=kb_source,
            top_k=_DEFAULT_CITATION_TOP_K,
        )
        citation_prompt_block = ""
        if citations:
            citation_lines: list[str] = []
            for idx, item in enumerate(citations, start=1):
                src = item.get("source", "unknown")
                chunk = item.get("chunk_index")
                chunk_text = "?" if chunk is None else str(chunk)
                quote = item.get("quote", "")
                citation_lines.append(f"{idx}. [{src}#{chunk_text}] {quote}")
            citation_prompt_block = "检索引用（用于追溯）：\n" + "\n".join(citation_lines) + "\n"

        system_prompt = (
            "你是“标书整合Agent”。请基于项目与已上传资料给出整合策略。"
            "输出纯文本，控制在200~400字。"
        )
        user_prompt = (
            f"项目：{filtered_tender.project_name}\n"
            f"包号：{target_packages}\n"
            f"企业：{company.name}\n"
            f"产品摘要：{json.dumps(product_summary, ensure_ascii=False)}\n"
            f"{citation_prompt_block}"
            "请给出：章节重点、资料落位建议、常见格式风险。"
        )

        integration_notes = _llm_call(self.llm, system_prompt, user_prompt)
        sections = generate_bid_sections(filtered_tender, raw_text, self.llm)

        return {
            "generated": True,
            "citations": citations,
            "integration_notes": integration_notes,
            "summary": f"已完成标书整合，共生成 {len(sections)} 个章节。",
        }, sections

    def step4_review_bid(
        self,
        tender: TenderDocument,
        analysis_result: dict[str, Any],
        validation_result: dict[str, Any],
        sections: list[BidDocumentSection],
        generation_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """第四步：审核标书并输出结论。"""
        if not sections:
            return _default_step4_if_blocked("未生成标书内容，无法执行审核。")

        section_digest = []
        for sec in sections:
            snippet = sec.content.replace("\n", " ").strip()
            if len(snippet) > _MAX_REVIEW_SECTION_CHARS:
                snippet = snippet[:_MAX_REVIEW_SECTION_CHARS] + "..."
            section_digest.append(
                {
                    "title": sec.section_title,
                    "snippet": snippet,
                }
            )

        system_prompt = (
            "你是“标书审核Agent”。根据招标要求、资料校验结果和标书内容，"
            "输出审核结论。只允许输出JSON。"
        )
        user_prompt = (
            "请输出JSON：\n"
            "{\n"
            '  "ready_for_submission": true/false,\n'
            '  "risk_level": "low/medium/high",\n'
            '  "compliance_score": 0-100,\n'
            '  "major_issues": ["..."],\n'
            '  "recommendations": ["..."],\n'
            '  "conclusion": "..."\n'
            "}\n\n"
            f"项目：{tender.project_name}\n"
            f"评分规则：{analysis_result.get('scoring_rules', [])}\n"
            f"资料校验结果：{json.dumps(validation_result, ensure_ascii=False)}\n"
            f"标书摘要：{json.dumps(section_digest, ensure_ascii=False)}\n"
            "要求：如发现占位符未填、证书资料留空、关键条款未响应，请明确指出。"
        )

        fallback = {
            "ready_for_submission": False,
            "risk_level": "medium",
            "compliance_score": 70.0,
            "major_issues": ["建议人工逐页核对占位符与附件是否已补齐。"],
            "recommendations": [
                "核对报价金额、交货期、质保期与招标文件是否一致。",
                "补齐证书与截图附件后再提交。",
            ],
            "conclusion": "初稿已生成，建议补齐缺失资料并完成人工终审后提交。",
        }

        try:
            content = _llm_call(self.llm, system_prompt, user_prompt)
            parsed = _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Step4 审核失败，使用默认结果：%s", exc)
            parsed = fallback

        ready = bool(parsed.get("ready_for_submission", False))
        risk_level = str(parsed.get("risk_level", "medium")).lower().strip()
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "medium"

        try:
            score = float(parsed.get("compliance_score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(100.0, score))

        major_issues = _ensure_str_list(parsed.get("major_issues"))
        recommendations = _ensure_str_list(parsed.get("recommendations"))
        conclusion = str(parsed.get("conclusion", "")).strip()

        if not major_issues:
            major_issues = fallback["major_issues"]
        if not recommendations:
            recommendations = fallback["recommendations"]
        if not conclusion:
            conclusion = fallback["conclusion"]

        second_validation = _second_validation(
            analysis_result=analysis_result,
            validation_result=validation_result,
            sections=sections,
            generation_result=generation_result,
        )

        if validation_result.get("overall_status") == "需补充":
            ready = False
            risk_level = "high"
            if "资料仍有缺失项，当前不建议提交。" not in major_issues:
                major_issues.insert(0, "资料仍有缺失项，当前不建议提交。")

        if second_validation.get("overall_status") == "需修订":
            ready = False
            risk_level = "high"
            score = min(score, 75.0)
            major_issues = _append_unique(major_issues, second_validation.get("issues", []))
            recommendations = _append_unique(recommendations, second_validation.get("suggestions", []))
            if "二次校验未通过，请先修订后再提交。" not in major_issues:
                major_issues.insert(0, "二次校验未通过，请先修订后再提交。")
            if "二次校验发现问题，需完成修订后再提交。" not in conclusion:
                conclusion = f"{conclusion} 二次校验发现问题，需完成修订后再提交。"

        return {
            "ready_for_submission": ready,
            "risk_level": risk_level,
            "compliance_score": score,
            "major_issues": major_issues,
            "recommendations": recommendations,
            "secondary_validation": second_validation,
            "conclusion": conclusion,
        }
