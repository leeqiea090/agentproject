from __future__ import annotations

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.table_builders as _table_builders

def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _table_builders,):
    __reexport_all(_module)

del _module
def _build_qualification_license_block(tender: TenderDocument) -> str:
    lines = [
        "### （一）投标公司资质-营业执照",
        "（此处留空，待上传证件）",
    ]

    if _is_medical_project(tender):
        lines.extend(
            [
                "",
                "### （二）投标公司资质-医疗器械经营许可证/备案凭证（如适用）",
                "（此处留空，待上传证件）",
                "",
                "### （三）生产厂家资质-营业执照",
                "（此处留空，待上传证件）",
                "",
                "### （四）生产厂家资质-医疗器械生产/经营许可文件（如适用）",
                "（此处留空，待上传证件）",
                "",
                "### （五）投标产品注册证/备案证明（如适用）",
                "（此处留空，待上传证件）",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "### （二）与项目相关的行业资质证书（如适用）",
                "（此处留空，待上传证件）",
                "",
                "### （三）质量管理体系或服务能力证明（如适用）",
                "（此处留空，待上传证件）",
            ]
        )

    lines.extend(
        [
            "",
            "### （六）投标产品授权文件",
            "（此处留空，待上传证件）",
        ]
    )

    if _has_imported_clues(tender):
        lines.extend(
            [
                "",
                "### （七）进口产品合法来源与报关资料（如适用）",
                "（此处留空，待上传证明材料）",
            ]
        )

    return "\n".join(lines)


def _build_enterprise_declaration_block(tender: TenderDocument, today: str) -> str:
    _ = tender
    return f"""## 八、企业类型声明函（分支选择）
请按企业实际情况勾选并提交对应材料：

### 分支A：中小企业声明函（货物/服务）
□ 适用。  
本公司郑重声明：本次投标所提供货物/服务由符合《中小企业划型标准规定》的企业制造/承接。
（此处留空，待按采购文件附表填写企业名称、从业人数、营业收入、资产总额等信息）

### 分支B：监狱企业证明材料
□ 适用。  
如本单位属于监狱企业，提交由省级以上监狱管理局、戒毒管理局（含新疆生产建设兵团）出具的证明文件。

### 分支C：残疾人福利性单位声明函
□ 适用。  
如本单位属于残疾人福利性单位，提交残疾人福利性单位声明函及相关证明材料。

### 分支D：非中小企业声明
□ 适用。  
本公司郑重声明：本次投标所提供货物/服务不属于中小企业政策优惠适用范围，并对声明真实性负责。

企业名称（盖章）：{_COMPANY}  
法定代表人或授权代表：{_AUTHORIZED_REP}  
日期：{today}"""


def _build_consortium_declaration_block(tender: TenderDocument, today: str) -> str:
    allows = _allow_consortium(tender)
    branch_b_hint = "如选择分支B，须同步提交联合体协议书及职责分工。" if allows else "分支B本项目不适用。"
    return f"""## 四、联合体投标声明（分支选择）
请按投标组织形式勾选：
- □ 分支A：本次以独立投标方式参与，不组成联合体；
- □ 分支B：本次以联合体方式参与；{branch_b_hint}

投标人名称：{_COMPANY}  
日期：{today}"""


def _build_detail_quote_table(
    tender: TenderDocument,
    tender_raw: str,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    lines = [
        "| 序号 | 货物名称 | 规格型号 | 生产厂家 | 品牌 | 单价(元) | 数量 | 总价(元) |",
        "|---:|---|---|---|---|---:|---|---:|",
    ]

    pkgs = packages if packages is not None else tender.packages
    if not pkgs:
        lines.append("| 1 | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] |")
        lines.append("|  | **合计报价** |  |  |  |  |  | **[待填写]** |")
        return "\n".join(lines)

    total_budget = 0.0
    for idx, pkg in enumerate(pkgs, start=1):
        total_budget += pkg.budget
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.append(
            f"| {idx} | {pkg.item_name} | [品牌型号] | [生产厂家] | [品牌] | [待填写] | "
            f"{quantity} | [待填写] |"
        )

    lines.append(f"|  | **预算合计（参考）** |  |  |  |  |  | **{_fmt_money(total_budget)}** |")
    lines.append("|  | **投标总报价** |  |  |  |  |  | **[待填写]** |")
    return "\n".join(lines)


def _gen_qualification(
    llm: ChatOpenAI,
    tender: TenderDocument,
    *,
    active_packages: list[ProcurementPackage] | None = None,
) -> BidDocumentSection:
    _ = llm
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    license_block = _build_qualification_license_block(tender)
    supplier_commitment_title = _supplier_commitment_title(tender)
    content = f"""## 一、符合《中华人民共和国政府采购法》第二十二条规定声明
{purchaser}：

{_COMPANY}参与贵方组织的“{tender.project_name}”（项目编号：{tender.project_number}，投标范围：{_package_scope(tender, active_packages)}）项目投标活动，现郑重声明如下：
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

## 二、{supplier_commitment_title}
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
{license_block}

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
    payment = _normalize_commitment_term(tender.commercial_terms.payment_method)
    validity = _safe_text(tender.commercial_terms.validity_period, "90日历天")
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    bond = _normalize_commitment_term(tender.commercial_terms.performance_bond, "按招标文件约定执行")
    consortium_block = _build_consortium_declaration_block(tender, today)
    enterprise_declaration_block = _build_enterprise_declaration_block(tender, today)
    medical_extra_block = ""
    if _is_medical_project(tender):
        medical_extra_block = f"""

## 九、医疗器械合规声明函（适用医疗项目）
我方声明：本次投标涉及的医疗器械产品在供货时将确保注册证/备案凭证、说明书、标签、合格证及追溯信息完整有效，且与投标型号一致。

投标人名称：{_COMPANY}  
日期：{today}"""

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

{consortium_block}

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

{enterprise_declaration_block}
{medical_extra_block}
"""
    return BidDocumentSection(section_title="第二章 符合性承诺", content=content.strip())


