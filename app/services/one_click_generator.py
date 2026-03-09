"""一键投标文件生成服务（按固定模板生成，强调格式稳定性）"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from langchain_openai import ChatOpenAI

from app.schemas import BidDocumentSection, ProcurementPackage, TenderDocument

logger = logging.getLogger(__name__)

_MAX_TECH_ROWS_PER_PACKAGE = 80

_COMPANY = "[投标方公司名称]"
_LEGAL_REP = "[法定代表人]"
_AUTHORIZED_REP = "[授权代表]"
_PHONE = "[联系电话]"
_ADDRESS = "[联系地址]"


def _today() -> str:
    return datetime.now().strftime("%Y年%m月%d日")


def _safe_text(text: str | None, default: str = "详见招标文件") -> str:
    if text is None:
        return default
    stripped = str(text).strip()
    return stripped or default


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts = [f"{k}：{_as_text(v)}" for k, v in value.items()]
        return "；".join(part for part in parts if part)
    if isinstance(value, list):
        return "；".join(_as_text(item) for item in value if _as_text(item))
    return str(value).strip()


def _fmt_money(amount: float) -> str:
    return f"{amount:,.2f}"


def _package_scope(tender: TenderDocument) -> str:
    if not tender.packages:
        return "全部包"
    return "、".join(f"包{pkg.package_id}" for pkg in tender.packages)


def _package_detail_lines(tender: TenderDocument) -> str:
    if not tender.packages:
        return "- 包信息：详见招标文件。"

    lines: list[str] = []
    for pkg in tender.packages:
        delivery = _safe_text(pkg.delivery_time, "按招标文件约定")
        place = _safe_text(pkg.delivery_place, "采购人指定地点")
        lines.append(
            f"- 包{pkg.package_id}：{pkg.item_name}；数量：{pkg.quantity}；预算：{_fmt_money(pkg.budget)}元；"
            f"交货期：{delivery}；交货地点：{place}"
        )
    return "\n".join(lines)


def _quote_overview_table(tender: TenderDocument) -> str:
    headers = [
        "| 序号(包号) | 货物名称 | 数量 | 预算金额(元) | 投标报价(元) | 交货期 |",
        "|---|---|---:|---:|---:|---|",
    ]
    rows: list[str] = []

    if tender.packages:
        total_budget = 0.0
        for idx, pkg in enumerate(tender.packages, start=1):
            total_budget += pkg.budget
            rows.append(
                f"| {idx}（{pkg.package_id}） | {pkg.item_name} | {pkg.quantity} | "
                f"{_fmt_money(pkg.budget)} | [待填写] | {_safe_text(pkg.delivery_time, '按招标文件约定')} |"
            )
        rows.append(
            f"|  | **合计** |  | **{_fmt_money(total_budget)}** | **[待填写]** |  |"
        )
    else:
        rows.append("| 1 | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] |")

    return "\n".join(headers + rows)


def _flatten_requirements(pkg: ProcurementPackage) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in pkg.technical_requirements.items():
        k = _safe_text(str(key), "技术参数")
        v = _safe_text(_as_text(value), "详见招标文件")
        items.append((k, v))
    return items


def _build_deviation_table(tender: TenderDocument, pkg: ProcurementPackage) -> str:
    lines = [
        f"### （一）技术偏离及详细配置明细表（第{pkg.package_id}包）",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        "",
        "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 偏离说明 |",
        "|---:|---|---|---|---|",
    ]

    requirements = _flatten_requirements(pkg)
    if not requirements:
        lines.append(
            "| 1 | 详见招标文件采购需求 | 完全响应，具体品牌型号参数待填写 | 无偏离 | 本项目参数逐条响应见后附技术资料 |"
        )
        return "\n".join(lines)

    for idx, (key, val) in enumerate(requirements[:_MAX_TECH_ROWS_PER_PACKAGE], start=1):
        req = f"{key}：{val}"
        lines.append(
            f"| {idx} | {req} | 完全响应，参数不低于招标要求（品牌型号：[品牌型号]） | 无偏离 | "
            "最终参数以产品彩页和技术文件为准 |"
        )

    if len(requirements) > _MAX_TECH_ROWS_PER_PACKAGE:
        lines.append(
            "|  | 其余技术参数 | 详见后附完整技术响应表 | 无偏离 | 本表仅展示核心参数，完整参数另附 |"
        )

    return "\n".join(lines)


def _build_configuration_table(pkg: ProcurementPackage) -> str:
    return "\n".join(
        [
            f"### （二）详细配置明细表（第{pkg.package_id}包）",
            "| 序号 | 配置名称 | 单位 | 数量 | 备注 |",
            "|---:|---|---|---:|---|",
            f"| 1 | {pkg.item_name}主机 | 台 | {pkg.quantity} | 核心设备 |",
            "| 2 | 配套软件系统 | 套 | 1 | 含安装、调试、授权 |",
            "| 3 | 随机附件及工具 | 套 | 1 | 按出厂标准配置 |",
            "| 4 | 技术文件（合格证/说明书等） | 套 | 1 | 交货时随货提供 |",
            "| 5 | 培训与验收服务 | 项 | 1 | 含现场培训与验收配合 |",
        ]
    )


def _build_main_parameter_table(pkg: ProcurementPackage) -> str:
    lines = [
        f"#### 包{pkg.package_id}：{pkg.item_name}",
        "| 序号 | 技术参数项 | 招标要求 | 响应情况 | 备注 |",
        "|---:|---|---|---|---|",
    ]

    requirements = _flatten_requirements(pkg)
    if not requirements:
        lines.append("| 1 | 核心技术参数 | 详见招标文件 | 完全响应（具体型号参数待填写） | 无偏离 |")
        return "\n".join(lines)

    for idx, (key, val) in enumerate(requirements[:_MAX_TECH_ROWS_PER_PACKAGE], start=1):
        lines.append(
            f"| {idx} | {key} | {val} | 满足或优于招标要求（具体参数待填写） | 无偏离 |"
        )

    if len(requirements) > _MAX_TECH_ROWS_PER_PACKAGE:
        lines.append("|  | 其余参数 | 详见附录参数表 | 全部响应 | 无偏离 |")

    return "\n".join(lines)


def _build_detail_quote_table(tender: TenderDocument) -> str:
    lines = [
        "| 序号 | 货物名称 | 规格型号 | 生产厂家 | 品牌 | 单价(元) | 数量 | 总价(元) |",
        "|---:|---|---|---|---|---:|---|---:|",
    ]

    if not tender.packages:
        lines.append("| 1 | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] |")
        lines.append("|  | **合计报价** |  |  |  |  |  | **[待填写]** |")
        return "\n".join(lines)

    total_budget = 0.0
    for idx, pkg in enumerate(tender.packages, start=1):
        total_budget += pkg.budget
        lines.append(
            f"| {idx} | {pkg.item_name} | [品牌型号] | [生产厂家] | [品牌] | [待填写] | "
            f"{pkg.quantity} | [待填写] |"
        )

    lines.append(
        f"|  | **预算合计（参考）** |  |  |  |  |  | **{_fmt_money(total_budget)}** |"
    )
    lines.append("|  | **投标总报价** |  |  |  |  |  | **[待填写]** |")
    return "\n".join(lines)


def _gen_qualification(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第一章：资格性证明文件"""
    _ = llm
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    content = f"""## 一、符合《中华人民共和国政府采购法》第二十二条规定声明
{purchaser}：

{_COMPANY}参与贵方组织的“{tender.project_name}”（项目编号：{tender.project_number}，投标范围：{_package_scope(tender)}）项目投标活动，现郑重声明如下：
1. 具备独立承担民事责任的能力；
2. 具有良好的商业信誉和健全的财务会计制度；
3. 具有履行合同所必需的设备和专业技术能力；
4. 具有依法缴纳税收和社会保障资金的良好记录；
5. 参加政府采购活动前三年内，在经营活动中没有重大违法记录；
6. 法律、行政法规规定的其他条件。

我方对上述声明内容的真实性负责，如有虚假，愿依法承担相应责任。

投标人名称：{_COMPANY}  
法定代表人或授权代表（签字）：{_AUTHORIZED_REP}  
日期：{today}  
（加盖公章）

## 二、黑龙江省政府采购供应商资格承诺函
我方作为政府采购供应商，现就供应商资格事项作出如下承诺：
1. 具有独立承担民事责任的能力，且经营状态合法有效；
2. 具有良好的商业信誉，未被列入失信被执行人名单；
3. 依法纳税、依法缴纳社会保障资金，相关记录可查询；
4. 具备履约所需的设备、人员与专业技术能力；
5. 参加本次政府采购活动前三年内无重大违法记录；
6. 不存在围标串标、弄虚作假等违法违规行为；
7. 承诺接受采购人及监管部门对承诺事项的核验；
8. 如承诺不实，愿承担相应法律责任及采购文件约定责任。

承诺人（供应商盖章）：{_COMPANY}  
日期：{today}

### （一）基本养老保险缴纳证明
（此处留空，待上传证明材料）

### （二）基本医疗保险及生育保险缴纳证明
（此处留空，待上传证明材料）

### （三）工伤保险缴纳证明
（此处留空，待上传证明材料）

### （四）失业保险缴纳证明
（此处留空，待上传证明材料）

## 三、承诺通过合法渠道可查证无行贿犯罪记录
{purchaser}：

我方承诺通过“中国执行信息公开网（http://zxgk.court.gov.cn）”等合法渠道，可查证法定代表人及单位负责人近三年内无行贿犯罪记录。
如有不实，我方愿承担由此产生的一切法律责任。

投标人名称：{_COMPANY}  
日期：{today}

### （一）全国企业信用信息公示系统截图
（此处留空，待上传截图）

### （二）中国执行信息公开网截图
（此处留空，待上传截图）

### （三）中国裁判文书网截图
（此处留空，待上传截图）

### （四）信用中国截图
（此处留空，待上传截图）

### （五）中国政府采购网截图
（此处留空，待上传截图）

## 四、其他承诺
{purchaser}：

我方承诺在本项目投标及合同履行过程中，严格遵循公平竞争、诚实信用、合法合规原则，不实施商业贿赂等违法违规行为；如有违反，愿承担全部法律后果。

投标人名称：{_COMPANY}  
日期：{today}

## 五、法定代表人授权书
{purchaser}：

兹授权{_AUTHORIZED_REP}为我单位本项目授权代表，参加“{tender.project_name}”（项目编号：{tender.project_number}）投标活动，并有权签署与本项目有关的各类文件。

法定代表人：{_LEGAL_REP}  
授权代表：{_AUTHORIZED_REP}  
联系电话：{_PHONE}  
联系地址：{_ADDRESS}  
投标人名称（盖章）：{_COMPANY}  
日期：{today}

## 六、法定代表人及授权代表身份证明
### （一）法定代表人身份证明
（此处留空，待上传法定代表人身份证正反面复印件）

### （二）授权代表身份证明
（此处留空，待上传授权代表身份证正反面复印件）

## 七、相关证件
### （一）投标公司资质-营业执照
（此处留空，待上传证件）

### （二）投标公司资质-医疗器械经营许可证/备案凭证（如适用）
（此处留空，待上传证件）

### （三）生产厂家资质-营业执照
（此处留空，待上传证件）

### （四）生产厂家资质-医疗器械生产/经营许可文件（如适用）
（此处留空，待上传证件）

### （五）投标产品注册证/备案证明（如适用）
（此处留空，待上传证件）

### （六）投标产品授权文件
（此处留空，待上传证件）

## 八、围标串标承诺函
{purchaser}：

我方郑重承诺，参与本项目投标过程中不存在围标、串标、弄虚作假等行为；若有违反，愿接受采购人及监管部门依法依规处理。

投标人名称：{_COMPANY}  
法定代表人或授权代表（签字）：{_AUTHORIZED_REP}  
日期：{today}
"""
    return BidDocumentSection(section_title="第一章 资格性证明文件", content=content.strip())


def _gen_compliance(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第二章：符合性承诺"""
    _ = llm
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    payment = _safe_text(tender.commercial_terms.payment_method, "按招标文件约定执行")
    validity = _safe_text(tender.commercial_terms.validity_period, "90日历天")
    warranty = _safe_text(tender.commercial_terms.warranty_period, "按招标文件约定执行")
    bond = _safe_text(tender.commercial_terms.performance_bond, "按招标文件约定执行")

    content = f"""## 一、投标报价承诺
{purchaser}：

我方承诺本项目报价真实、完整、唯一且具有竞争性，不存在低于成本恶意报价、围标串标、虚假报价等行为。投标报价已充分考虑运输、安装、调试、培训、税费及售后服务等全部费用。

投标人名称：{_COMPANY}  
日期：{today}

## 二、投标文件规范性、符合性承诺
我方承诺：投标文件的签署、盖章、装订、密封、递交、响应格式及内容均符合采购文件要求，对采购文件提出的实质性条款已逐项响应，不存在重大偏离。

投标人名称：{_COMPANY}  
日期：{today}

## 三、满足主要商务条款的承诺书
我方承诺对以下商务条款作出实质性响应并严格履行：
1. 付款方式：{payment}
2. 投标有效期：{validity}
3. 质保期：{warranty}
4. 履约保证金：{bond}
5. 交货期限与地点：按招标文件及合同约定执行
6. 其他商务要求：如验收、违约责任、售后条款等均按招标文件及合同条款执行

投标人名称：{_COMPANY}  
授权代表：{_AUTHORIZED_REP}  
日期：{today}

## 四、联合体投标声明
我方声明：本次投标为独立投标，非联合体投标。

投标人名称：{_COMPANY}  
日期：{today}

## 五、技术部分实质性内容承诺
我方承诺：所投产品或服务对招标文件技术条款逐条响应，满足（或优于）采购文件要求；如出现偏离，将在“技术偏离表”中如实披露并说明原因。

投标人名称：{_COMPANY}  
日期：{today}

## 六、其他要求承诺
我方承诺遵守招标文件关于诚信投标、廉洁投标、知识产权、信息安全和保密义务等全部要求，不实施影响采购公平性的行为。

投标人名称：{_COMPANY}  
日期：{today}

## 七、投标人关联单位说明
我方承诺如实披露与本单位存在下列关系的单位：
1. 与投标人单位负责人为同一人的其他单位：[待填写]
2. 与投标人存在直接控股、管理关系的其他单位：[待填写]

投标人名称：{_COMPANY}  
日期：{today}

## 八、非中小企业声明函
本公司郑重声明：本次投标所提供货物/服务的企业规模属性情况如下（按采购文件要求填写），并对声明内容的真实性负责。

企业名称（盖章）：{_COMPANY}  
法定代表人或授权代表：{_AUTHORIZED_REP}  
日期：{today}
"""
    return BidDocumentSection(section_title="第二章 符合性承诺", content=content.strip())


def _gen_technical(llm: ChatOpenAI, tender: TenderDocument, tender_raw: str) -> BidDocumentSection:
    """第三章：商务及技术部分"""
    _ = llm
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    package_details = _package_detail_lines(tender)
    quote_table = _quote_overview_table(tender)

    technical_sections: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            technical_sections.append(_build_deviation_table(tender, pkg))
            technical_sections.append(_build_configuration_table(pkg))
    else:
        technical_sections.append(
            "\n".join(
                [
                    "### （一）技术偏离及详细配置明细表",
                    "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 偏离说明 |",
                    "|---:|---|---|---|---|",
                    "| 1 | 详见招标文件 | 完全响应（具体参数待填写） | 无偏离 | - |",
                ]
            )
        )

    content = f"""## 一、报价书
{purchaser}：

我方{_COMPANY}已详细研究“{tender.project_name}”（项目编号：{tender.project_number}）采购文件，愿按采购文件及合同条款要求提供合格货物及服务，并承担相应责任义务。现提交报价文件如下：
1. 投标范围：{_package_scope(tender)}
2. 报价原则：满足招标文件实质性条款，报价包含货物、运输、安装、调试、培训、税费、售后服务等全部费用
3. 履约承诺：严格按合同约定进度组织供货、安装、验收及售后服务
4. 有效期承诺：投标有效期按招标文件约定执行

采购包信息摘要：
{package_details}

投标人名称：{_COMPANY}  
授权代表：{_AUTHORIZED_REP}  
联系电话：{_PHONE}  
日期：{today}

## 二、报价一览表
项目名称：{tender.project_name}  
项目编号：{tender.project_number}

{quote_table}

## 三、技术偏离及详细配置明细表
{"\n\n".join(technical_sections)}

> 说明：本章已按“逐包、逐参数”形式编制。若采购文件另有固定格式，以采购文件格式为准。
> 招标原文长度：{len(tender_raw)} 字符（用于内容校验与追溯）。
"""
    return BidDocumentSection(section_title="第三章 商务及技术部分", content=content.strip())


def _gen_appendix(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第四章：报价书附件（技术参数明细 + 售后服务方案）"""
    _ = llm
    today = _today()
    warranty = _safe_text(tender.commercial_terms.warranty_period, "按招标文件约定执行")
    payment = _safe_text(tender.commercial_terms.payment_method, "按招标文件约定执行")

    parameter_tables: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            parameter_tables.append(_build_main_parameter_table(pkg))
    else:
        parameter_tables.append(
            "\n".join(
                [
                    "#### 包信息",
                    "| 序号 | 技术参数项 | 招标要求 | 响应情况 | 备注 |",
                    "|---:|---|---|---|---|",
                    "| 1 | 核心参数 | 详见招标文件 | 完全响应（参数待填写） | 无偏离 |",
                ]
            )
        )

    content = f"""## 一、产品主要技术参数明细表及报价表
### （一）产品主要技术参数
{"\n\n".join(parameter_tables)}

### （二）报价明细表
项目名称：{tender.project_name}  
项目编号：{tender.project_number}

{_build_detail_quote_table(tender)}

## 二、技术服务和售后服务的内容及措施
### （一）技术服务
1. 安装调试服务：设备到货后安排专业工程师现场安装、调试并协助完成验收；
2. 培训服务：提供操作培训、日常维护培训和故障初判培训，确保使用科室独立开展工作；
3. 技术咨询服务：提供7×24小时电话/线上技术支持，必要时提供现场技术支持；
4. 质量保障服务：供货产品均为全新合格产品，随机文件齐全，来源可追溯；
5. 交付配合服务：根据采购人计划安排发运、卸货、安装和交接，保障项目按期落地。

### （二）售后服务
1. 质保期承诺：{warranty}；
2. 响应时限：接到通知后4小时内响应，24小时内提供现场处置或明确解决方案；
3. 维护保养：每年至少2次预防性巡检维护，形成维护记录；
4. 配件保障：提供常用备件保障及更换服务，确保设备持续稳定运行；
5. 质保期外服务：继续提供长期技术支持，收费标准公开透明；
6. 商务执行：付款方式按“{payment}”及合同约定执行。

投标人名称：{_COMPANY}  
授权代表：{_AUTHORIZED_REP}  
日期：{today}

## 三、产品彩页
（此处留空，待上传产品彩页）

## 四、节能认证证书
（此处留空，待上传节能/环保/能效认证证书）

## 五、检测/质评数据节选
（此处留空，待上传检测报告或室间质评结果）
"""
    return BidDocumentSection(section_title="第四章 报价书附件", content=content.strip())


def generate_bid_sections(
    tender: TenderDocument,
    tender_raw: str,
    llm: ChatOpenAI,
) -> list[BidDocumentSection]:
    """
    根据招标文件生成全部投标文件章节。

    Args:
        tender: 结构化招标文件数据
        tender_raw: 招标文件原始文本（供技术章节追溯）
        llm: 语言模型实例（为兼容接口保留）

    Returns:
        各章节列表
    """
    logger.info("开始一键生成投标文件章节")
    logger.info("招标原文长度：%d 字符", len(tender_raw))

    sections = [
        _gen_qualification(llm, tender),
        _gen_compliance(llm, tender),
        _gen_technical(llm, tender, tender_raw),
        _gen_appendix(llm, tender),
    ]

    logger.info("一键投标文件章节生成完成，共 %d 章", len(sections))
    return sections
