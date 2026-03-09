"""一键投标文件生成服务 - 仅需招标文件即可生成完整投标文件"""
from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.schemas import BidDocumentSection, TenderDocument

logger = logging.getLogger(__name__)

# 全文截取上限（字符）
_MAX_TENDER_CHARS = 30000


def _llm_call(llm: ChatOpenAI, system: str, user: str) -> str:
    resp = llm.invoke([SystemMessage(system), HumanMessage(user)])
    content = resp.content
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return str(content).strip()


# ─────────────────────────────────────────────────────────────
# 各章节生成函数
# ─────────────────────────────────────────────────────────────

def _gen_qualification(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第一章：资格性证明文件"""
    today = datetime.now().strftime("%Y年%m月%d日")
    system = """你是专业的政府采购投标文件撰写专家，请按照真实的政府采购投标文件格式和规范生成对应章节内容。
输出使用Markdown格式，语气正式专业，内容完整详实，符合《政府采购法》要求。"""
    user = f"""请根据以下招标信息，生成"第一章 资格性证明文件"的完整内容。

招标项目名称：{tender.project_name}
项目编号：{tender.project_number}
采购人：{tender.purchaser}
代理机构：{tender.agency}
当前日期：{today}

注意：企业信息用[投标方公司名称]、[法定代表人]、[联系电话]等占位符标注，方便投标方填写真实信息。

该章节需包含以下全部内容（每个小节用##标题分隔）：
## 一、符合《中华人民共和国政府采购法》第二十二条规定声明
## 二、黑龙江省政府采购供应商资格承诺函（包含独立承担民事责任、商业信誉良好、依法缴纳税收和社会保障资金、三年内无重大违法记录等承诺条款，内容详实完整）
## 三、承诺无行贿犯罪记录（说明可通过中国执行信息公开网等平台查证）
## 四、其他承诺（廉洁投标承诺等）
## 五、法定代表人授权书
## 六、法定代表人身份证明
## 七、相关证件（列明需附的证件清单）
## 八、围标串标承诺函

每个承诺函需包含：承诺方（[投标方公司名称]）、日期、签章占位符。"""

    content = _llm_call(llm, system, user)
    return BidDocumentSection(section_title="第一章 资格性证明文件", content=content)


def _gen_compliance(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第二章：符合性承诺"""
    today = datetime.now().strftime("%Y年%m月%d日")
    packages_str = "、".join(
        [f"包{p.package_id}（{p.item_name}）" for p in tender.packages]
    ) or "全部包"

    system = """你是专业的政府采购投标文件撰写专家。
输出使用Markdown格式，内容完整、语气正式，严格对应招标文件要求。"""
    user = f"""请根据以下招标信息，生成"第二章 符合性承诺"的完整内容。

项目名称：{tender.project_name}
项目编号：{tender.project_number}
采购人：{tender.purchaser}
采购方式：{tender.procurement_type}
付款方式：{tender.commercial_terms.payment_method}
投标有效期：{tender.commercial_terms.validity_period}
质保期：{tender.commercial_terms.warranty_period}
履约保证金：{tender.commercial_terms.performance_bond}
投标包：{packages_str}
日期：{today}

企业信息用[投标方公司名称]等占位符。

该章节需包含（每个小节用##标题分隔）：
## 一、投标报价承诺（承诺报价真实、不围标串标、不低于成本等）
## 二、投标文件规范性、符合性承诺（承诺满足招标文件所有实质性要求）
## 三、满足主要商务条款的承诺书（逐条响应付款方式、交期、质保期等）
## 四、联合体投标（声明本次为非联合体投标）
## 五、技术部分实质性内容承诺（承诺所投产品满足全部技术参数）
## 六、其他要求承诺（廉洁、诚信等）
## 七、投标人关联单位的说明
## 八、非中小企业声明函"""

    content = _llm_call(llm, system, user)
    return BidDocumentSection(section_title="第二章 符合性承诺", content=content)


def _gen_technical(llm: ChatOpenAI, tender: TenderDocument, tender_raw: str) -> BidDocumentSection:
    """第三章：商务及技术部分"""
    today = datetime.now().strftime("%Y年%m月%d日")
    # 整理采购包信息
    packages_detail = []
    for p in tender.packages:
        tech_items = "\n".join(
            f"  - {k}：{v}" for k, v in p.technical_requirements.items()
        ) or "  （详见招标文件）"
        packages_detail.append(
            f"**包{p.package_id}：{p.item_name}**  数量：{p.quantity}  预算：{p.budget:,.2f}元\n"
            f"技术要求：\n{tech_items}"
        )
    packages_str = "\n\n".join(packages_detail) if packages_detail else "（以招标文件为准）"

    system = """你是专业的医疗设备投标文件撰写专家和技术工程师。
输出使用Markdown格式，技术内容专业详实，表格整齐规范。"""
    user = f"""请根据以下招标信息，生成"第三章 商务及技术部分"的完整内容。

项目名称：{tender.project_name}
项目编号：{tender.project_number}
采购人：{tender.purchaser}
日期：{today}

采购包详情：
{packages_str}

总预算：{tender.budget:,.2f}元

要求：
- 企业名称用[投标方公司名称]，产品品牌型号用[品牌型号]等占位符
- 报价在预算范围内，可略低于预算（体现竞争力）

该章节需包含（每个小节用##标题分隔）：
## 一、报价书
（含正式的报价声明：承诺按照谈判文件要求提供货物及服务，报价不高于预算，价格包含所有费用）

## 二、报价一览表
（Markdown表格：包号 | 货物名称 | 数量 | 单价(元) | 合价(元)  ，最后一行合计）

## 三、技术偏离及详细配置明细表
（对每个采购包，用Markdown表格列出：序号 | 招标技术参数要求 | 投标产品参数 | 是否偏离 | 偏离说明
  参数响应应完整、专业，尽量响应所有技术要求，"是否偏离"列默认填"无偏离"）"""

    content = _llm_call(llm, system, user)
    return BidDocumentSection(section_title="第三章 商务及技术部分", content=content)


def _gen_appendix(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第四章：报价书附件（技术参数明细 + 售后服务方案）"""
    today = datetime.now().strftime("%Y年%m月%d日")
    packages_detail = []
    for p in tender.packages:
        tech_items = "\n".join(
            f"  - {k}：{v}" for k, v in p.technical_requirements.items()
        ) or "  （详见招标文件）"
        packages_detail.append(
            f"**包{p.package_id}：{p.item_name}**  数量：{p.quantity}\n技术要求：\n{tech_items}"
        )
    packages_str = "\n\n".join(packages_detail) if packages_detail else "（以招标文件为准）"

    system = """你是专业的医疗设备投标文件撰写专家。
输出使用Markdown格式，内容专业详实，售后服务方案具体可执行。"""
    user = f"""请根据以下招标信息，生成"第四章 报价书附件"的完整内容。

项目名称：{tender.project_name}
项目编号：{tender.project_number}
采购人：{tender.purchaser}
质保期：{tender.commercial_terms.warranty_period}
日期：{today}

采购包详情：
{packages_str}

企业信息用[投标方公司名称]等占位符。

该章节需包含（每个小节用##标题分隔）：
## 一、产品主要技术参数及报价表
（对每个采购包，用Markdown表格列出详细技术参数响应及最终报价明细）

## 二、技术服务和售后服务的内容及措施
### （一）技术服务
（安装调试服务：免费上门安装、调试、验收；培训服务：操作培训、维护培训；技术咨询：全年7×24小时电话技术支持等）

### （二）售后服务
（质保期内：免费维修、零配件更换；响应时间：接到通知后4小时内响应，24小时内到场；定期保养：每年2次免费预防性维护；备件支持；应急预案等。内容详实、承诺具体。）"""

    content = _llm_call(llm, system, user)
    return BidDocumentSection(section_title="第四章 报价书附件", content=content)


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────

def generate_bid_sections(
    tender: TenderDocument,
    tender_raw: str,
    llm: ChatOpenAI,
) -> list[BidDocumentSection]:
    """
    根据招标文件生成全部投标文件章节。

    Args:
        tender: 结构化招标文件数据
        tender_raw: 招标文件原始文本（供技术章节参考）
        llm: 语言模型实例

    Returns:
        各章节列表
    """
    logger.info("开始一键生成投标文件章节")
    sections: list[BidDocumentSection] = []

    logger.info("生成第一章：资格性证明文件")
    sections.append(_gen_qualification(llm, tender))

    logger.info("生成第二章：符合性承诺")
    sections.append(_gen_compliance(llm, tender))

    # 限制原始文本长度，避免 token 过多
    raw_short = tender_raw[:_MAX_TENDER_CHARS]
    logger.info("生成第三章：商务及技术部分")
    sections.append(_gen_technical(llm, tender, raw_short))

    logger.info("生成第四章：报价书附件")
    sections.append(_gen_appendix(llm, tender))

    logger.info("一键投标文件章节生成完成，共 %d 章", len(sections))
    return sections
