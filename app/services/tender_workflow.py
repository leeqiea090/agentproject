"""招投标四阶段正式工作流服务。"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.schemas import BidDocumentSection, CompanyProfile, ProductSpecification, TenderDocument
from app.services.one_click_generator import generate_bid_sections

logger = logging.getLogger(__name__)

_MAX_RAW_PROMPT_CHARS = 24000
_MAX_REVIEW_SECTION_CHARS = 1800


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
        "conclusion": "当前不具备提交条件。",
    }


class TenderWorkflowAgent:
    """四阶段招投标工作流 Agent。"""

    def __init__(self, llm: ChatOpenAI):
        self.llm = llm

    def step1_analyze_tender(self, tender: TenderDocument, raw_text: str) -> dict[str, Any]:
        """第一步：解析招标文件并提炼关键结果。"""
        fallback = _default_step1_result(tender)
        raw_excerpt = (raw_text or "")[:_MAX_RAW_PROMPT_CHARS]
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

        return {
            "key_information": key_info,
            "required_materials": required_materials,
            "scoring_rules": scoring_rules,
            "risk_alerts": risk_alerts,
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

        system_prompt = (
            "你是“标书整合Agent”。请基于项目与已上传资料给出整合策略。"
            "输出纯文本，控制在200~400字。"
        )
        user_prompt = (
            f"项目：{filtered_tender.project_name}\n"
            f"包号：{target_packages}\n"
            f"企业：{company.name}\n"
            f"产品摘要：{json.dumps(product_summary, ensure_ascii=False)}\n"
            "请给出：章节重点、资料落位建议、常见格式风险。"
        )

        integration_notes = _llm_call(self.llm, system_prompt, user_prompt)
        sections = generate_bid_sections(filtered_tender, raw_text, self.llm)

        return {
            "generated": True,
            "integration_notes": integration_notes,
            "summary": f"已完成标书整合，共生成 {len(sections)} 个章节。",
        }, sections

    def step4_review_bid(
        self,
        tender: TenderDocument,
        analysis_result: dict[str, Any],
        validation_result: dict[str, Any],
        sections: list[BidDocumentSection],
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

        if validation_result.get("overall_status") == "需补充":
            ready = False
            risk_level = "high"
            if "资料仍有缺失项，当前不建议提交。" not in major_issues:
                major_issues.insert(0, "资料仍有缺失项，当前不建议提交。")

        return {
            "ready_for_submission": ready,
            "risk_level": risk_level,
            "compliance_score": score,
            "major_issues": major_issues,
            "recommendations": recommendations,
            "conclusion": conclusion,
        }
