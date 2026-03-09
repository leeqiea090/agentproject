"""投标文件生成Agent - 使用LangGraph编排多个子Agent"""
from typing import Any, TypedDict, Annotated
import operator
from datetime import datetime
import logging

from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.schemas import (
    TenderDocument,
    CompanyProfile,
    ProductSpecification,
    BidDocumentSection,
    BidGenerateRequest
)

logger = logging.getLogger(__name__)


class BidGenerationState(TypedDict):
    """投标文件生成的状态"""
    # 输入
    tender_doc: TenderDocument
    company_profile: CompanyProfile
    products: dict[str, ProductSpecification]  # package_id -> product
    request: BidGenerateRequest

    # 中间结果
    sections: Annotated[list[BidDocumentSection], operator.add]
    current_section: str
    errors: Annotated[list[str], operator.add]

    # 输出
    bid_id: str
    status: str


class BidGeneratorAgent:
    """投标文件生成主Agent"""

    def __init__(self, llm: ChatOpenAI | None = None):
        """
        初始化投标文件生成Agent

        Args:
            llm: 语言模型实例
        """
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0.3)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """构建LangGraph工作流"""
        workflow = StateGraph(BidGenerationState)

        # 添加节点（各个生成步骤）
        workflow.add_node("generate_qualification", self.generate_qualification_section)
        workflow.add_node("generate_compliance", self.generate_compliance_section)
        workflow.add_node("generate_technical", self.generate_technical_section)
        workflow.add_node("generate_commercial", self.generate_commercial_section)
        workflow.add_node("generate_service", self.generate_service_section)
        workflow.add_node("finalize", self.finalize_bid)

        # 定义流程
        workflow.set_entry_point("generate_qualification")
        workflow.add_edge("generate_qualification", "generate_compliance")
        workflow.add_edge("generate_compliance", "generate_technical")
        workflow.add_edge("generate_technical", "generate_commercial")
        workflow.add_edge("generate_commercial", "generate_service")
        workflow.add_edge("generate_service", "finalize")
        workflow.add_edge("finalize", END)

        return workflow.compile()

    # ============ 各章节生成函数 ============

    def generate_qualification_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成第一章：资格性证明文件"""
        logger.info("生成资格性证明文件章节")

        company = state["company_profile"]
        tender = state["tender_doc"]

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一位专业的投标文件撰写专家。请根据企业信息生成"资格性证明文件"章节。

该章节通常包括：
1. 符合《中华人民共和国政府采购法》第二十二条规定的声明
2. 营业执照说明
3. 项目人员清单
4. 政府采购供应商资格承诺函
5. 社保缴纳证明说明
6. 信用查询说明（全国企业信用信息公示系统、中国执行信息公开网等）
7. 法定代表人授权书
8. 相关资质证件说明
9. 围标串标承诺函

要求：
- 语气正式、专业
- 格式规范，符合政府采购要求
- 突出企业资质齐全、信誉良好
- 每个部分用Markdown二级标题分隔"""),
            ("user", """企业信息：
- 企业名称：{company_name}
- 法定代表人：{legal_rep}
- 地址：{address}
- 电话：{phone}
- 资质证照数量：{license_count}

项目信息：
- 项目名称：{project_name}
- 项目编号：{project_number}

请生成"第一章 资格性证明文件"的内容（Markdown格式）：""")
        ])

        chain = prompt | self.llm
        response = chain.invoke({
            "company_name": company.name,
            "legal_rep": company.legal_representative,
            "address": company.address,
            "phone": company.phone,
            "license_count": len(company.licenses),
            "project_name": tender.project_name,
            "project_number": tender.project_number
        })

        section = BidDocumentSection(
            section_title="第一章 资格性证明文件",
            content=response.content,
            attachments=[lic.file_path for lic in company.licenses if lic.file_path]
        )

        return {
            "sections": [section],
            "current_section": "qualification"
        }

    def generate_compliance_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成第二章：符合性承诺"""
        logger.info("生成符合性承诺章节")

        tender = state["tender_doc"]
        company = state["company_profile"]
        request = state["request"]

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是投标文件撰写专家。请生成"符合性承诺"章节。

该章节包括：
1. 投标报价承诺
2. 投标文件规范性、符合性承诺
3. 满足主要商务条款的承诺书
4. 联合体投标说明（通常为"非联合体"）
5. 技术部分实质性内容承诺
6. 其他要求承诺

每个承诺都要：
- 明确响应招标文件要求
- 格式规范
- 包含承诺人签字日期等格式要素"""),
            ("user", """项目信息：
- 项目名称：{project_name}
- 项目编号：{project_number}

商务条款：
- 付款方式：{payment_method}
- 投标有效期：{validity_period}
- 履约保证金：{performance_bond}

投标包号：{packages}

企业名称：{company_name}

请生成"第二章 符合性承诺"的内容（Markdown格式）：""")
        ])

        chain = prompt | self.llm
        response = chain.invoke({
            "project_name": tender.project_name,
            "project_number": tender.project_number,
            "payment_method": tender.commercial_terms.payment_method,
            "validity_period": tender.commercial_terms.validity_period,
            "performance_bond": tender.commercial_terms.performance_bond,
            "packages": ", ".join(request.selected_packages),
            "company_name": company.name
        })

        section = BidDocumentSection(
            section_title="第二章 符合性承诺",
            content=response.content
        )

        return {
            "sections": [section],
            "current_section": "compliance"
        }

    def generate_technical_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成第三章：商务及技术部分"""
        logger.info("生成商务及技术部分章节")

        tender = state["tender_doc"]
        products = state["products"]
        request = state["request"]

        # 对每个投标包生成技术响应
        technical_responses = []

        for package_id in request.selected_packages:
            # 找到对应的采购包
            package = next((p for p in tender.packages if p.package_id == package_id), None)
            if not package:
                continue

            # 找到对应的产品
            product = products.get(package_id)
            if not product:
                continue

            prompt = ChatPromptTemplate.from_messages([
                ("system", """你是医疗设备技术专家。请生成技术响应内容，包括：
1. 产品基本信息（品牌、型号、产地等）
2. 技术偏离表（逐条对比招标要求和产品参数）
3. 详细配置清单
4. 技术优势说明

要求：
- 准确对比招标要求和产品参数
- 如有偏离需明确说明，否则标注"无偏离"
- 突出产品技术优势
- 使用表格形式（Markdown格式）"""),
                ("user", """采购包信息：
- 包号：{package_id}
- 货物名称：{item_name}
- 数量：{quantity}
- 技术要求：{tech_req}

我方产品：
- 产品名称：{product_name}
- 生产厂家：{manufacturer}
- 产地：{origin}
- 型号：{model}
- 技术参数：{specs}

请生成该包的技术响应内容（Markdown格式，包含技术偏离表）：""")
            ])

            chain = prompt | self.llm
            response = chain.invoke({
                "package_id": package.package_id,
                "item_name": package.item_name,
                "quantity": package.quantity,
                "tech_req": str(package.technical_requirements),
                "product_name": product.product_name,
                "manufacturer": product.manufacturer,
                "origin": product.origin,
                "model": product.model,
                "specs": str(product.specifications)
            })

            technical_responses.append(f"\n\n### 包{package_id}：{package.item_name}\n\n{response.content}")

        full_content = "\n".join(technical_responses)

        section = BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content=full_content
        )

        return {
            "sections": [section],
            "current_section": "technical"
        }

    def generate_commercial_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成报价部分（包含在第三章或第四章）"""
        logger.info("生成商务报价部分")

        tender = state["tender_doc"]
        products = state["products"]
        request = state["request"]

        # 计算报价
        quotations = []
        total_price = 0.0

        for package_id in request.selected_packages:
            package = next((p for p in tender.packages if p.package_id == package_id), None)
            product = products.get(package_id)

            if package and product:
                unit_price = product.price * request.discount_rate
                subtotal = unit_price * package.quantity
                total_price += subtotal

                quotations.append({
                    "package_id": package_id,
                    "item_name": package.item_name,
                    "quantity": package.quantity,
                    "unit_price": unit_price,
                    "subtotal": subtotal
                })

        # 生成报价表格
        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是财务报价专家。请根据报价数据生成标准的投标报价书和报价一览表。

要求：
- 使用表格格式（Markdown）
- 价格精确到小数点后2位
- 包含：包号、货物名称、数量、单价、小计
- 最后一行是"合计"
- 大写金额：人民币XXX万XXX千XXX元整"""),
            ("user", """报价数据：
{quotations}

总价：{total_price}元

请生成报价书和报价一览表（Markdown格式）：""")
        ])

        chain = prompt | self.llm
        response = chain.invoke({
            "quotations": str(quotations),
            "total_price": total_price
        })

        section = BidDocumentSection(
            section_title="报价书及报价一览表",
            content=response.content
        )

        return {
            "sections": [section],
            "current_section": "commercial"
        }

    def generate_service_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成售后服务方案"""
        logger.info("生成售后服务方案章节")

        tender = state["tender_doc"]
        request = state["request"]
        custom_plan = request.custom_service_plan

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是医疗设备售后服务方案专家。请生成详细的售后服务承诺。

内容包括：
1. 技术服务
   - 安装调试
   - 人员培训
   - 技术咨询
2. 售后服务
   - 质保期承诺
   - 响应时间
   - 维修保养计划
   - 备件供应
   - 应急预案

要求：
- 专业、详细
- 承诺具体、可执行
- 突出服务优势"""),
            ("user", """项目信息：
- 项目名称：{project_name}
- 设备类型：{equipment_types}

{custom_instruction}

请生成"技术服务和售后服务方案"（Markdown格式）：""")
        ])

        equipment_types = ", ".join([
            p.item_name for p in tender.packages
            if p.package_id in request.selected_packages
        ])

        custom_instruction = f"特别要求：{custom_plan}" if custom_plan else "请使用标准服务方案"

        chain = prompt | self.llm
        response = chain.invoke({
            "project_name": tender.project_name,
            "equipment_types": equipment_types,
            "custom_instruction": custom_instruction
        })

        section = BidDocumentSection(
            section_title="第四章 技术服务和售后服务",
            content=response.content
        )

        return {
            "sections": [section],
            "current_section": "service"
        }

    def finalize_bid(self, state: BidGenerationState) -> dict[str, Any]:
        """完成投标文件生成，添加目录和封面"""
        logger.info("完成投标文件组装")

        tender = state["tender_doc"]
        company = state["company_profile"]

        # 生成封面
        cover_content = f"""# 政府采购响应文件

## 项目名称：{tender.project_name}

## 项目编号：{tender.project_number}

---

**供应商全称**：{company.name}（公章）

**授权代表**：{company.legal_representative}

**电话**：{company.phone}

**日期**：{datetime.now().strftime('%Y年%m月%d日')}

---
"""

        cover_section = BidDocumentSection(
            section_title="封面",
            content=cover_content
        )

        # 生成目录
        toc_lines = ["# 目录\n"]
        for i, section in enumerate(state["sections"], 1):
            toc_lines.append(f"{i}. {section.section_title}")

        toc_section = BidDocumentSection(
            section_title="目录",
            content="\n".join(toc_lines)
        )

        return {
            "sections": [cover_section, toc_section],
            "status": "completed",
            "bid_id": f"BID_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        }

    def generate(self, state: BidGenerationState) -> BidGenerationState:
        """
        执行投标文件生成

        Args:
            state: 初始状态

        Returns:
            最终状态
        """
        try:
            final_state = self.graph.invoke(state)
            logger.info(f"投标文件生成完成，共 {len(final_state['sections'])} 个章节")
            return final_state
        except Exception as e:
            logger.error(f"投标文件生成失败: {str(e)}")
            raise


def create_bid_generator(llm: ChatOpenAI | None = None) -> BidGeneratorAgent:
    """
    工厂函数：创建投标文件生成Agent

    Args:
        llm: 可选的LLM实例

    Returns:
        BidGeneratorAgent实例
    """
    return BidGeneratorAgent(llm)
