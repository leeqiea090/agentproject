from __future__ import annotations

from langchain_openai import ChatOpenAI

import app.services.tender_workflow.common as _common
import app.services.tender_workflow.classification as _classification
import app.services.tender_workflow.product_facts as _product_facts
import app.services.tender_workflow.evidence as _evidence
import app.services.tender_workflow.materialization as _materialization
import app.services.tender_workflow.sanitization as _sanitization
import app.services.tender_workflow.reporting as _reporting
import app.services.tender_workflow.validation as _validation
import importlib

from app.schemas import TenderDocument


def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _classification, _product_facts, _evidence, _materialization, _sanitization, _reporting, _validation,):
    __reexport_all(_module)

del _module

def _workflow_api():
    """动态导入正式工作流聚合模块。"""
    return importlib.import_module("app.services.tender_workflow")
class TenderWorkflowAgent:
    """十层招投标工作流 Agent。"""

    def __init__(self, llm: ChatOpenAI):
        """初始化TenderWorkflowAgent。"""
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
            '  "offered_facts": [\n'
            '    {"fact_name": "...", "fact_value": "...", "source_excerpt": "..."}\n'
            '  ],\n'
            '  "scoring_rules": ["..."],\n'
            '  "risk_alerts": ["..."],\n'
            '  "summary": "..."\n'
            "}\n\n"
            "要求：\n"
            "1. key_information需包含项目名称、项目编号、采购人、采购方式、预算、包信息、核心商务条款；\n"
            "2. required_materials必须是可执行的资料清单；\n"
            "3. offered_facts 从招标原文中提取采购方已明示的允许值/范围/规格，"
            "例如允许的品牌范围、参数上下限、明确说明[须提供XX]的事实性描述，"
            "每条包含 fact_name、fact_value、source_excerpt 三个字段，最多提取20条；\n"
            "4. scoring_rules从评分标准中提炼，若文件无明确权重请说明；\n"
            "5. risk_alerts给出3~6条最关键风险提示。\n\n"
            f"结构化招标信息：\n{json.dumps(tender.model_dump(), ensure_ascii=False)}\n\n"
            f"招标原文（截断）：\n{raw_excerpt}"
        )
        try:
            content = _workflow_api()._llm_call(self.llm, system_prompt, user_prompt)
            parsed = _extract_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Step1 解析失败，使用默认结果：%s", exc)
            return fallback

        key_info = parsed.get("key_information")
        required_materials = _ensure_str_list(parsed.get("required_materials"))
        raw_offered_facts = parsed.get("offered_facts")
        offered_facts: list[dict[str, Any]] = (
            [
                item for item in raw_offered_facts
                if isinstance(item, dict)
                and _safe_text(item.get("fact_name"))
                and _safe_text(item.get("fact_value"))
            ]
            if isinstance(raw_offered_facts, list)
            else []
        )
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
        if offered_facts:
            summary = f"{summary}；已从原文提取 {len(offered_facts)} 条 offered facts。"

        return {
            "key_information": key_info,
            "required_materials": required_materials,
            "offered_facts": offered_facts,
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
        context = _workflow_context_text(tender)
        medical_project = _contains_any(context, _MEDICAL_KEYWORDS)
        imported_project = _contains_any(context, _IMPORTED_KEYWORDS)
        requires_energy_cert = _contains_any(context, ("节能", "环保", "能效"))

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

            if medical_project:
                has_registration = bool(product.registration_number.strip())
                checklist.append(
                    _material_item(
                        f"包{pkg_id} 医疗器械注册证/备案",
                        "通过" if has_registration else "缺失",
                        f"注册证/备案编号：{product.registration_number or '-'}",
                        "医疗器械项目需补充注册证或备案凭证编号及对应附件。",
                    )
                )

            if imported_project:
                has_origin = bool(product.origin.strip())
                checklist.append(
                    _material_item(
                        f"包{pkg_id} 原产地/合法来源",
                        "通过" if has_origin else "缺失",
                        f"原产地：{product.origin or '-'}",
                        "进口项目需补充原产地、合法来源或报关相关说明。",
                    )
                )
                has_authorization = bool(product.authorization_letter.strip())
                checklist.append(
                    _material_item(
                        f"包{pkg_id} 授权链/报关材料",
                        "通过" if has_authorization else "缺失",
                        f"授权材料：{product.authorization_letter or '-'}",
                        "进口项目需补充厂家授权、报关单或同等效力材料。",
                    )
                )

            if requires_energy_cert:
                has_certifications = bool(product.certifications)
                checklist.append(
                    _material_item(
                        f"包{pkg_id} 节能环保认证",
                        "通过" if has_certifications else "缺失",
                        f"认证数量：{len(product.certifications)}",
                        "存在节能/环保/能效要求时需补充对应认证材料。",
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
            content = _workflow_api()._llm_call(self.llm, system_prompt, user_prompt)
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

    def step3_classify_clauses(
        self,
        tender: TenderDocument,
        analysis_result: dict[str, Any],
        selected_packages: list[str],
        raw_text: str,
    ) -> dict[str, Any]:
        """第三步：条款分类与分支决策。"""
        return _classify_clauses(
            tender=tender,
            analysis_result=analysis_result,
            selected_packages=selected_packages,
            raw_text=raw_text,
        )

    def step4_normalize_requirements(
        self,
        tender: TenderDocument,
        analysis_result: dict[str, Any],
        clause_result: dict[str, Any],
        selected_packages: list[str],
        raw_text: str,
    ) -> dict[str, Any]:
        """第四步：将条款归一化为可机读字段。"""
        return _normalize_requirements(
            tender=tender,
            analysis_result=analysis_result,
            clause_result=clause_result,
            selected_packages=selected_packages,
            raw_text=raw_text,
        )

    def step5_decide_rules(
        self,
        tender: TenderDocument,
        raw_text: str,
        selected_packages: list[str],
        company: CompanyProfile | None,
        products: dict[str, ProductSpecification],
        clause_result: dict[str, Any],
    ) -> dict[str, Any]:
        """第五步：通过规则引擎固化关键分支。"""
        return _decide_rule_branches(
            tender=tender,
            raw_text=raw_text,
            selected_packages=selected_packages,
            company=company,
            products=products,
            clause_result=clause_result,
        )

    def step4_bind_evidence(
        self,
        tender: TenderDocument,
        raw_text: str,
        analysis_result: dict[str, Any],
            company: CompanyProfile | None = None,
        products: dict[str, ProductSpecification] | None = None,
        selected_packages: list[str] | None = None,
        normalized_result: dict[str, Any] | None = None,
        product_fact_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """第六步：将关键需求绑定到招标原文与投标方证据。"""
        return _build_evidence_bindings(
            tender=tender,
            raw_text=raw_text,
            company=company,
            products=products,
            selected_packages=selected_packages,
            normalized_result=normalized_result,
            product_fact_result=product_fact_result,
        )

    def step3_integrate_bid(
        self,
        tender: TenderDocument,
        raw_text: str,
        selected_packages: list[str],
        company: CompanyProfile,
        products: dict[str, ProductSpecification],
        kb_source: str | None = None,
        analysis_result: dict[str, Any] | None = None,
        product_fact_result: dict[str, Any] | None = None,
        normalized_result: dict[str, Any] | None = None,
        evidence_result: dict[str, Any] | None = None,
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

        # Build offered_facts block so Writer has ground-truth values and skips placeholders
        all_offered_facts: list[dict[str, Any]] = []
        if analysis_result and isinstance(analysis_result.get("offered_facts"), list):
            all_offered_facts.extend(analysis_result["offered_facts"])
        if product_fact_result and isinstance(product_fact_result.get("packages"), list):
            for pkg_entry in product_fact_result["packages"]:
                if not isinstance(pkg_entry, dict):
                    continue
                for fact in pkg_entry.get("offered_facts", []):
                    if isinstance(fact, dict):
                        all_offered_facts.append(fact)
        seen_fact_keys: set[str] = set()
        fact_lines: list[str] = []
        for fact in all_offered_facts:
            name = _safe_text(fact.get("fact_name") or fact.get("evidence_type"))
            value = _safe_text(fact.get("fact_value") or fact.get("evidence_value"))
            source = _safe_text(fact.get("evidence_source", ""))
            key = f"{name}::{value}"
            if not name or not value or key in seen_fact_keys:
                continue
            seen_fact_keys.add(key)
            line = f"- {name}：{value}"
            if source:
                line += f"（来源：{source}）"
            fact_lines.append(line)
        offered_facts_prompt_block = ""
        if fact_lines:
            offered_facts_prompt_block = (
                "已知可用真值（请直接填入正文，禁止使用[待填写]/[待补充]等占位符）：\n"
                + "\n".join(fact_lines[:40])
                + "\n"
            )

        # Build structured product profile block for Writer
        product_profile_prompt_block = ""
        if product_fact_result:
            profile_block = _build_product_profile_block(product_fact_result)
            if profile_block:
                product_profile_prompt_block = (
                    "投标产品档案（请基于以下信息填写品牌、型号、厂家、参数，禁止使用占位符）：\n"
                    + profile_block + "\n"
                )

        system_prompt = (
            "你是[标书整合Agent]。请基于项目与已上传资料给出整合策略。"
            "输出纯文本，控制在200~400字。"
            "重要：已知真值信息中有数据时，必须直接引用，不得使用[待填写]等占位符。"
        )
        user_prompt = (
            f"项目：{filtered_tender.project_name}\n"
            f"包号：{target_packages}\n"
            f"企业：{company.name}\n"
            f"产品摘要：{json.dumps(product_summary, ensure_ascii=False)}\n"
            f"{product_profile_prompt_block}"
            f"{offered_facts_prompt_block}"
            f"{citation_prompt_block}"
            "请给出：章节重点、资料落位建议、常见格式风险。"
        )

        integration_notes = _workflow_api()._llm_call(self.llm, system_prompt, user_prompt)
        product_profiles = {
            pkg_id: _build_product_profile(product)
            for pkg_id, product in products.items()
            if product is not None
        }
        gen_result = generate_bid_sections(
            filtered_tender,
            raw_text,
            self.llm,
            products=products,
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profiles=product_profiles,
            required_materials=analysis_result.get("required_materials"),
        )
        sections = gen_result.sections

        return {
            "generated": True,
            "citations": citations,
            "selected_packages": target_packages,
            "product_summary": product_summary,
            "offered_facts_injected": len(fact_lines),
            "integration_notes": integration_notes,
            "summary": (
                f"已完成标书整合，共生成 {len(sections)} 个章节；"
                f"已向 Writer 注入 {len(fact_lines)} 条 offered facts。"
            ),
        }, sections

    def step6_validate_consistency(
        self,
        analysis_result: dict[str, Any],
        validation_result: dict[str, Any],
        sections: list[BidDocumentSection],
        generation_result: dict[str, Any] | None = None,
        tender: TenderDocument | None = None,
        selected_packages: list[str] | None = None,
        products: dict[str, ProductSpecification] | None = None,
        evidence_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """第八步：发布前硬校验。"""
        return _second_validation(
            analysis_result=analysis_result,
            validation_result=validation_result,
            sections=sections,
            generation_result=generation_result,
            tender=tender,
            selected_packages=selected_packages,
            products=products,
            evidence_result=evidence_result,
        )

    def step4_review_bid(
        self,
        tender: TenderDocument,
        analysis_result: dict[str, Any],
        validation_result: dict[str, Any],
        sections: list[BidDocumentSection],
        generation_result: dict[str, Any] | None = None,
        selected_packages: list[str] | None = None,
        products: dict[str, ProductSpecification] | None = None,
        evidence_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """辅助审核：生成 review 摘要字段。"""
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
            "请输出JSON，结构如下：\n"
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
            "risk_level": "high",
            "compliance_score": 0.0,
            "major_issues": ["建议人工逐页核对占位符与附件是否已补齐。"],
            "recommendations": [
                "核对报价金额、交货期、质保期与招标文件是否一致。",
                "补齐证书与截图附件后再提交。",
            ],
            "conclusion": "初稿已生成，建议补齐缺失资料并完成人工终审后提交。",
        }

        try:
            content = _workflow_api()._llm_call(self.llm, system_prompt, user_prompt)
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
            tender=tender,
            selected_packages=selected_packages,
            products=products,
            evidence_result=evidence_result,
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

    def step8_sanitize_outbound(
        self,
        sections: list[BidDocumentSection],
        hard_validation_result: dict[str, Any] | None = None,
        evidence_result: dict[str, Any] | None = None,
        normalized_result: dict[str, Any] | None = None,
    ) -> tuple[list[BidDocumentSection], dict[str, Any]]:
        """第九步：外发净化。"""
        return _sanitize_for_external_delivery(
            sections=sections,
            hard_validation_result=hard_validation_result,
            evidence_result=evidence_result,
            normalized_result=normalized_result,
        )

    def step9_regression(
        self,
        stages: list[dict[str, Any]],
        consistency_result: dict[str, Any] | None,
        review_result: dict[str, Any] | None,
        sanitize_result: dict[str, Any] | None,
        evidence_result: dict[str, Any] | None,
        *,
        normalized_result: dict[str, Any] | None = None,
        product_fact_result: dict[str, Any] | None = None,
        sections: list[BidDocumentSection] | None = None,
        selected_packages: list[str] | None = None,
        tender: TenderDocument | None = None,
    ) -> dict[str, Any]:
        """第九步：评测回归。"""
        return _build_regression_report(
            stages=stages,
            consistency_result=consistency_result,
            review_result=review_result,
            sanitize_result=sanitize_result,
            evidence_result=evidence_result,
            normalized_result=normalized_result,
            product_fact_result=product_fact_result,
            sections=sections,
            selected_packages=selected_packages,
            tender=tender,
        )
