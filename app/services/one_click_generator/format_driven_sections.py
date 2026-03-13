from __future__ import annotations

from app.schemas import BidDocumentSection, TenderDocument, ProcurementPackage


def build_format_driven_sections(
    tender: TenderDocument,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list[ProcurementPackage] | None = None,
) -> list[BidDocumentSection]:
    packages = active_packages or tender.packages
    sections: list[BidDocumentSection] = []

    sections.append(BidDocumentSection(
        section_title="一、响应文件封面格式",
        content=f"""
政 府 采 购
响 应 文 件

项目名称：{tender.project_name}
项目编号：{tender.project_number}

供应商全称（公章）：【待填写：投标人名称】
授权代表：【待填写：授权代表】
电话：【待填写：联系电话】
日期：【待填写：日期】
""".strip()
    ))

    sections.append(BidDocumentSection(
        section_title="二、首轮报价表",
        content="采用电子招投标的项目无需自行编制，按投标客户端报价部分填写。"
    ))

    sections.append(BidDocumentSection(
        section_title="三、分项报价表",
        content="采用电子招投标的项目无需自行编制，按投标客户端报价部分填写。"
    ))

    sections.append(BidDocumentSection(
        section_title="四、技术偏离及详细配置明细表",
        content="""
| 序号 | 服务名称 | 磋商文件的服务需求 | 响应文件响应情况 | 偏离情况 |
|---:|---|---|---|---|
| 1 | 【待填写：服务名称】 | 【待填写：招标文件要求】 | 【待填写：逐条响应内容】 | 【待填写：无偏离/正偏离/负偏离】 |
""".strip()
    ))

    sections.append(BidDocumentSection(
        section_title="五、技术服务和售后服务的内容及措施",
        content="""
#### 1. 供货组织措施
【待填写：供货组织安排】

#### 2. 到货与安装调试措施
【待填写：到货、安装、调试安排】

#### 3. 培训措施
【待填写：使用培训、工程师培训】

#### 4. 售后响应措施
【待填写：响应时限、维修支持、保养频次、升级服务】
""".strip()
    ))

    sections.append(BidDocumentSection(
        section_title="六、法定代表人/单位负责人授权书",
        content=f"""
（报价单位全称）法定代表人/单位负责人授权【待填写：授权代表姓名】为供应商代表，
参加贵处组织的 {tender.project_name}（项目编号：{tender.project_number}）采购活动，
全权处理本活动中的一切事宜。
""".strip()
    ))

    sections.append(BidDocumentSection(
        section_title="附：资格性审查响应对照表",
        content="""
| 序号 | 审查项 | 招标文件要求 | 响应情况 | 对应材料/页码 |
|---:|---|---|---|---|
| 1 | 【待填写】 | 【待填写】 | 【待填写】 | 【待填写】 |
""".strip()
    ))

    sections.append(BidDocumentSection(
        section_title="附：符合性审查响应对照表",
        content="""
| 序号 | 审查项 | 招标文件要求 | 响应情况 | 对应材料/页码 |
|---:|---|---|---|---|
| 1 | 【待填写】 | 【待填写】 | 【待填写】 | 【待填写】 |
""".strip()
    ))

    sections.append(BidDocumentSection(
        section_title="附：详细评审响应对照表",
        content="""
| 序号 | 评审项 | 评审标准 | 响应情况 | 对应材料/页码 |
|---:|---|---|---|---|
| 1 | 【待填写】 | 【待填写】 | 【待填写】 | 【待填写】 |
""".strip()
    ))

    sections.append(BidDocumentSection(
        section_title="附：投标无效情形汇总及自检表",
        content="""
| 序号 | 无效情形 | 自检结果 | 备注 |
|---:|---|---|---|
| 1 | 【待填写：招标文件规定的无效投标情形】 | 【待填写】 | 【待填写】 |
""".strip()
    ))

    return sections