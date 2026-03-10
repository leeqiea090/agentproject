from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import BidDocumentSection, CompanyLicense, CompanyProfile, ProductSpecification, ProcurementPackage, TenderDocument
from app.routers import tender as tender_api


def _sample_tender() -> TenderDocument:
    return TenderDocument(
        project_name="医院流式细胞分析仪采购项目",
        project_number="HLJ-2026-018",
        budget=1200000.0,
        purchaser="某三甲医院",
        agency="某招标代理",
        procurement_type="公开招标",
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="进口流式细胞分析仪",
                quantity=1,
                budget=1200000.0,
                technical_requirements={"激光器": "≥3", "荧光通道": "≥11"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            )
        ],
        evaluation_criteria={"技术分": 60, "价格分": 30, "商务分": 10},
        special_requirements="本项目适用中小企业政策，不接受联合体投标，允许采购进口设备。",
    )


def _fake_llm_call(llm, system_prompt: str, user_prompt: str) -> str:  # noqa: ANN001
    if "招标解析Agent" in system_prompt:
        return (
            '{'
            '"key_information":{"project_name":"医院流式细胞分析仪采购项目","project_number":"HLJ-2026-018","purchaser":"某三甲医院","procurement_type":"公开招标","budget":1200000,"packages":[{"package_id":"1","item_name":"进口流式细胞分析仪","quantity":1,"budget":1200000}],"commercial_terms":{"payment_method":"验收合格后付款"}},'
            '"required_materials":["营业执照","法定代表人授权书","产品注册证","技术偏离表"],'
            '"scoring_rules":["技术分：60","价格分：30","商务分：10"],'
            '"risk_alerts":["注意注册证与授权链时效","注意报价与数量一致性"],'
            '"summary":"已提取项目关键信息。"}'
        )
    if "资料校验Agent" in system_prompt:
        return (
            '{"overall_status":"通过","summary":"资料完整，可继续生成。","missing_items":[],"next_actions":["继续执行生成与校验流程。"]}'
        )
    if "标书整合Agent" in system_prompt:
        return "章节重点已根据企业资质、产品参数与评分重点完成整理。"
    if "标书审核Agent" in system_prompt:
        return (
            '{"ready_for_submission":true,"risk_level":"low","compliance_score":91,'
            '"major_issues":[],"recommendations":["执行人工终审后提交。"],"conclusion":"自动审核完成。"}'
        )
    raise AssertionError(system_prompt)


def test_workflow_run_api_returns_ten_stages_and_dual_outputs() -> None:
    tender = _sample_tender()
    raw_text = (
        "包1 进口流式细胞分析仪\n"
        "技术参数：激光器≥3；荧光通道≥11。\n"
        "交货期：合同签订后30日内。\n"
        "本项目适用中小企业政策，不接受联合体投标，允许采购进口设备。\n"
    )
    company = CompanyProfile(
        company_id="company-1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
        licenses=[
            CompanyLicense(
                license_type="营业执照",
                license_number="91110101TEST",
                valid_until="长期",
            )
        ],
        social_insurance_proof="data/company/social-proof.pdf",
        credit_check_time=datetime(2026, 3, 1, 9, 0, 0),
    )
    product = ProductSpecification(
        product_id="product-1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        origin="美国",
        model="FC5000",
        specifications={"激光器": "3个独立激光器", "荧光通道": "12个"},
        price=1000000.0,
        certifications=["CE认证", "ISO13485"],
        registration_number="国械注进20260001",
        authorization_letter="data/product/auth.pdf",
    )

    tender_api.tender_storage.clear()
    tender_api.company_storage.clear()
    tender_api.product_storage.clear()
    tender_api.bid_storage.clear()
    tender_api.workflow_storage.clear()
    tender_api.workflow_kb_indexed_sources.clear()

    tender_api.tender_storage["tender-1"] = {
        "tender_id": "tender-1",
        "status": "parsed",
        "parsed_data": tender.model_dump(),
        "raw_text": raw_text,
        "upload_time": datetime.now(),
    }
    tender_api.company_storage["company-1"] = company
    tender_api.product_storage["product-1"] = product

    client = TestClient(app)
    with (
        patch.object(tender_api, "get_chat_model", return_value=object()),
        patch.object(
            tender_api,
            "ingest_text_to_kb",
            return_value={"source": "tender::tender-1", "chunks_indexed": 1, "total_characters": len(raw_text)},
        ),
        patch("app.services.tender_workflow._llm_call", side_effect=_fake_llm_call),
        patch(
            "app.services.tender_workflow.search_knowledge",
            return_value=[
                {
                    "text": "本项目适用中小企业政策，不接受联合体投标，允许采购进口设备。",
                    "score": 0.91,
                    "metadata": {"source": "tender::tender-1", "chunk_index": 0},
                }
            ],
        ),
    ):
        response = client.post(
            "/api/tender/workflow/run",
            json={
                "tender_id": "tender-1",
                "company_profile_id": "company-1",
                "selected_packages": ["1"],
                "product_ids": {"1": "product-1"},
                "continue_on_material_gaps": False,
                "generate_docx": False,
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()

    assert len(payload["stages"]) == 11
    assert [stage["stage_code"] for stage in payload["stages"]] == [
        "document_ingestion",
        "package_segmentation",
        "clause_classification",
        "requirement_normalization",
        "detail_expansion",
        "rule_decision",
        "evidence_binding",
        "chapter_generation",
        "hard_validation",
        "dual_output",
        "evaluation_regression",
    ]

    dual_output = payload["stages"][9]["data"]
    assert "internal_audit" in dual_output
    assert "external_delivery" in dual_output
    assert dual_output["internal_audit"]["selected_packages"] == ["1"]
    assert dual_output["internal_audit"]["product_fact_count"] >= 6
    assert dual_output["internal_audit"]["proven_completion_rate"] == 1.0
    assert "section_titles" in dual_output["external_delivery"]
    normalization_stage = payload["stages"][3]["data"]
    evidence_stage = payload["stages"][6]["data"]
    assert "product_fact_extraction" in normalization_stage
    assert "response_value_hints" in normalization_stage
    assert normalization_stage["product_fact_extraction"]["fact_count"] >= 6
    assert "requirement_product_matching" in evidence_stage
    assert evidence_stage["proven_completion_rate"] == 1.0

    bid_id = payload["generation"]["bid_id"]
    assert bid_id in tender_api.bid_storage
    stored_content = "\n".join(section["content"] for section in tender_api.bid_storage[bid_id]["sections"])
    assert "测试医疗科技有限公司" in stored_content
    assert "FC5000" in stored_content
    assert "国械注进20260001" in stored_content
    assert "拟投产品实参摘要" in stored_content


def test_bid_generate_api_uses_same_deep_materialization() -> None:
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="company-2",
        name="测试医疗科技有限公司",
        legal_representative="李四",
        address="长春市高新区示范路2号",
        phone="13900000000",
        licenses=[
            CompanyLicense(
                license_type="营业执照",
                license_number="91110101TEST02",
                valid_until="长期",
            )
        ],
        social_insurance_proof="data/company/social-proof-2.pdf",
    )
    product = ProductSpecification(
        product_id="product-2",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        origin="美国",
        model="FC6000",
        specifications={"激光器": "4个独立激光器", "荧光通道": "14个"},
        price=1100000.0,
        certifications=["CE认证"],
        registration_number="国械注进20260002",
        authorization_letter="data/product/auth-2.pdf",
    )

    tender_api.tender_storage.clear()
    tender_api.company_storage.clear()
    tender_api.product_storage.clear()
    tender_api.bid_storage.clear()

    tender_api.tender_storage["tender-2"] = {
        "tender_id": "tender-2",
        "status": "parsed",
        "parsed_data": tender.model_dump(),
        "raw_text": "包1 进口流式细胞分析仪\n技术参数：激光器≥3；荧光通道≥11。",
        "upload_time": datetime.now(),
    }
    tender_api.company_storage["company-2"] = company
    tender_api.product_storage["product-2"] = product

    class _FakeGenerator:
        def generate(self, state):  # noqa: ANN001
            return {
                "bid_id": "bid-legacy-1",
                "sections": [
                    BidDocumentSection(
                        section_title="第一章 资格性证明文件",
                        content=(
                            "投标人名称：[投标方公司名称]\n"
                            "### （一）基本养老保险缴纳证明\n"
                            "（此处留空，待上传证明材料）\n"
                        ),
                        attachments=[],
                    ),
                    BidDocumentSection(
                        section_title="第三章 商务及技术部分",
                        content=(
                            "### （一）技术偏离及详细配置明细表（第1包）\n"
                            "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 响应依据/证据映射 |\n"
                            "|---:|---|---|---|---|\n"
                            "| 1 | 激光器：≥3 | ≥3 | 无偏离 | 招标原文片段 |\n"
                        ),
                        attachments=[],
                    ),
                    BidDocumentSection(
                        section_title="第四章 报价书附件",
                        content=(
                            "## 一、产品主要技术参数明细表及报价表\n"
                            "| 序号 | 货物名称 | 规格型号 | 生产厂家 | 品牌 | 单价(元) | 数量 | 总价(元) |\n"
                            "|---:|---|---|---|---|---:|---|---:|\n"
                            "| 1 | 进口流式细胞分析仪 | [品牌型号] | [生产厂家] | [品牌] | [待填写] | 1 | [待填写] |\n"
                            "## 三、产品彩页\n"
                            "（此处留空，待上传产品彩页）\n"
                        ),
                        attachments=[],
                    ),
                ],
            }

    client = TestClient(app)
    with (
        patch.object(tender_api, "get_chat_model", return_value=object()),
        patch.object(tender_api, "create_bid_generator", return_value=_FakeGenerator()),
    ):
        response = client.post(
            "/api/tender/bid/generate",
            json={
                "tender_id": "tender-2",
                "company_profile_id": "company-2",
                "selected_packages": ["1"],
                "product_ids": {"1": "product-2"},
                "discount_rate": 1.0,
                "add_performance_cases": False,
                "custom_service_plan": "",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    combined = "\n".join(section["content"] for section in payload["sections"])
    assert "materialize_report" in payload
    assert "consistency_report" in payload
    assert "outbound_report" in payload
    assert "changed_sections" in payload["materialize_report"]
    assert "unresolved_sections" in payload["materialize_report"]
    assert "overall_status" in payload["consistency_report"]
    assert "status" in payload["outbound_report"]
    assert "测试医疗科技有限公司" in combined
    assert "已关联社保缴纳证明：data/company/social-proof-2.pdf" in combined
    assert "4个独立激光器" in combined
    assert "FC6000" in combined
    assert "已关联彩页资料：流式细胞分析仪 / FC6000 / 某厂家" in combined
    assert "待核实（未匹配到已证实产品事实）" not in combined
    assert "第一章 资格性证明文件" in payload["materialize_report"]["changed_sections"]
    assert payload["outbound_report"]["status"] in {"通过", "需人工终审", "阻断外发"}

    stored = tender_api.bid_storage["bid-legacy-1"]
    assert "materialize_report" in stored
    assert "consistency_report" in stored
    assert "outbound_report" in stored
    stored_combined = "\n".join(section["content"] for section in stored["sections"])
    assert "国械注进20260002" in stored_combined

    detail_response = client.get("/api/tender/bid/bid-legacy-1")
    assert detail_response.status_code == 200, detail_response.text
    detail_payload = detail_response.json()
    assert detail_payload["materialize_report"]["changed_sections"] == payload["materialize_report"]["changed_sections"]
    assert detail_payload["consistency_report"]["overall_status"] == payload["consistency_report"]["overall_status"]


def test_bid_generate_api_does_not_expose_external_draft_when_blocked() -> None:
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="company-blocked",
        name="测试医疗科技有限公司",
        legal_representative="王五",
        address="长春市高新区示范路3号",
        phone="13700000000",
    )
    product = ProductSpecification(
        product_id="product-blocked",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        origin="美国",
        model="FC7000",
        specifications={"激光器": "4个独立激光器"},
        price=1200000.0,
        registration_number="国械注进20260003",
        authorization_letter="data/product/auth-3.pdf",
    )

    tender_api.tender_storage.clear()
    tender_api.company_storage.clear()
    tender_api.product_storage.clear()
    tender_api.bid_storage.clear()

    tender_api.tender_storage["tender-blocked"] = {
        "tender_id": "tender-blocked",
        "status": "parsed",
        "parsed_data": tender.model_dump(),
        "raw_text": "包1 进口流式细胞分析仪\n技术参数：激光器≥3；荧光通道≥11。",
        "upload_time": datetime.now(),
    }
    tender_api.company_storage["company-blocked"] = company
    tender_api.product_storage["product-blocked"] = product

    class _BlockedGenerator:
        def generate(self, state):  # noqa: ANN001
            return {
                "bid_id": "bid-blocked-1",
                "sections": [
                    BidDocumentSection(
                        section_title="第一章 资格性证明文件",
                        content=(
                            "你是投标文件专家。\n"
                            "投标人名称：[投标方公司名称]\n"
                            "[待填写]\n"
                        ),
                        attachments=[],
                    )
                ],
            }

    client = TestClient(app)
    with (
        patch.object(tender_api, "get_chat_model", return_value=object()),
        patch.object(tender_api, "create_bid_generator", return_value=_BlockedGenerator()),
    ):
        response = client.post(
            "/api/tender/bid/generate",
            json={
                "tender_id": "tender-blocked",
                "company_profile_id": "company-blocked",
                "selected_packages": ["1"],
                "product_ids": {"1": "product-blocked"},
                "discount_rate": 1.0,
                "add_performance_cases": False,
                "custom_service_plan": "",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["outbound_report"]["status"] == "阻断外发"
    assert payload["outbound_report"]["generated"] is False
    assert payload["outbound_report"]["section_titles"] == []
    assert payload["download_url"] == ""

    combined = "\n".join(section["content"] for section in payload["sections"])
    assert "你是投标文件专家" in combined
    assert "[待填写]" in combined

    stored = tender_api.bid_storage["bid-blocked-1"]
    assert stored["outbound_report"]["status"] == "阻断外发"
    assert stored["outbound_report"]["section_titles"] == []
    stored_combined = "\n".join(section["content"] for section in stored["sections"])
    assert "你是投标文件专家" in stored_combined
