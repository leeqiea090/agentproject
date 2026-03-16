"""格式驱动章节生成的公共工具与共享提取逻辑。"""
from __future__ import annotations

import re

from app.schemas import BidDocumentSection, TenderDocument, ProcurementPackage
__all__ = [
    "re",
    "BidDocumentSection",
    "TenderDocument",
    "ProcurementPackage",
    "_build_affiliated_units_statement_template",
    "_build_hlj_supplier_qualification_commitment_template",
    "_build_manufacturer_authorization_template",
    "_build_service_fee_commitment_template",
    "_build_small_enterprise_declaration_template",
    "_build_disabled_unit_declaration_template",
    "_build_service_supply_points",
    "_build_service_packaging_points",
    "_build_service_installation_points",
    "_build_service_training_points",
    "_build_service_acceptance_points",
    "_build_service_after_sales_points",
    "_build_service_quality_points",
    "_renumber_numbered_points",
    "_normalize_hlj_supplier_qualification_template",
    "_extract_review_block",
    "_extract_review_rows_from_block",
    "_clean_text",
    "_md_table",
    "_row_to_cells",
    "_pick_template_rows",
    "_extract_labeled_block",
    "_parse_named_rows",
    "_dedupe_named_rows",
    "_normalize_detailed_review_key",
    "_is_valid_invalid_item",
    "_normalize_number_text",
    "_extract_package_summary_rows",
    "_find_summary_row",
    "_extract_package_quantity",
    "_extract_delivery_time",
    "_extract_delivery_place",
    "_extract_package_budget",
    "_budget_text",
    "_extract_requirements_chapter",
    "_find_package_block",
    "_extract_detail_quantity",
    "_merge_numbered_lines",
    "_extract_tech_points",
    "_extract_service_points",
    "_build_quote_summary_table",
    "_build_pkg_deviation_table",
    "_build_service_section",
    "_extract_anchor_block",
    "_merge_bullet_lines",
    "_split_review_row",
    "_build_review_table_markdown",
    "_extract_review_rows_from_tender",
    "_extract_invalid_bid_items",
    "_extract_scoring_items",
    "_build_detailed_review_section",
]


def _project_name_text(tender) -> str:
    return getattr(tender, "project_name", "") or "【待填写：项目名称】"


def _project_number_text(tender) -> str:
    return getattr(tender, "project_number", "") or getattr(tender, "project_no", "") or "【待填写：项目编号】"


def _purchaser_text(tender) -> str:
    return getattr(tender, "purchaser", "") or "【待填写：采购人】"


def _agency_text(tender) -> str:
    return getattr(tender, "agency", "") or "【待填写：代理机构】"


def _goods_names_text(packages: list[ProcurementPackage] | None = None) -> str:
    names = [getattr(pkg, "item_name", "") for pkg in (packages or []) if getattr(pkg, "item_name", "")]
    return "、".join(names) if names else "【待填写：货物名称】"


def _service_plan_text(value: str, fallback: str) -> str:
    text = _clean_text(value)
    return text if text else fallback


def _build_service_supply_points(
    item_name: str,
    delivery_time: str,
    delivery_place: str,
    *,
    product_identity: str = "",
    spec_digest: str = "",
) -> list[str]:
    item = _service_plan_text(item_name, "本包设备")
    goods = _service_plan_text(product_identity, item)
    schedule = _service_plan_text(delivery_time, "采购文件约定时限")
    place = _service_plan_text(delivery_place, "采购人指定地点")
    specs = _service_plan_text(spec_digest, "采购文件及投标响应确认的配置、附件和随机资料")
    return [
        f"1）项目组织与职责分工：围绕{goods}成立专项实施小组，配置项目负责人、商务对接、物流协调、安装调试工程师、培训工程师和售后服务负责人，形成供货、安装、培训、验收、维保一体化工作机制。",
        f"2）进度计划与节点控制：以“{schedule}”为总控目标，收到中标/合同通知后立即启动锁货、备货复核、发运审批、到货预约和现场进场计划，确保{item}按期送达{place}。",
        f"3）发货前复核：发运前逐项核对货物名称、型号规格、数量、序列号/批号、随机附件、随机文件和外观状态，确保{specs}与投标响应保持一致。",
        "4）协调机制与进度风险预警：到货前与采购人、使用科室及相关保障部门确认卸货时间、搬运路线、暂存位置和进场窗口；如遇排产延迟、物流拥堵、天气异常等情况，立即启动改线运输、优先发运、加派人员或分批到货等补救措施。",
    ]


def _build_service_packaging_points(
    item_name: str,
    *,
    product_identity: str = "",
) -> list[str]:
    item = _service_plan_text(item_name, "本包设备")
    goods = _service_plan_text(product_identity, item)
    return [
        f"1）包装标准与加固要求：{goods}按原厂标准包装或不低于原厂标准的加固方案实施，落实防震、防潮、防雨、防压、防倒置、防碰撞等措施，对精密部件、易损部件和随机附件分别进行缓冲、防护和固定。",
        f"2）外箱标识与单据管理：外包装清晰标注项目名称、设备名称、型号规格、数量、收货单位、收货地址、重心方向及轻放防潮等运输标识，并随货附装箱单、配货单、附件清单和必要的交接单据。",
        f"3）运输组织与在途监控：根据{item}体积、重量、时效和防护要求选择合规车辆或物流渠道，发运后持续跟踪运输节点，确保途中搬运、装卸、转运和临时存放环节受控。",
        "4）到货保护与异常处理：货物到场后先核查外包装完整性、封签状态、箱号数量和外观情况，再组织开箱点验；如发现破损、受潮、缺件、短少或异常污染，立即拍照留痕并启动补发、换货、维修或整改流程，未完成处置前不转入正式验收。",
    ]


def _build_service_installation_points(
    item_name: str,
    *,
    delivery_place: str = "",
    functional_notes: str = "",
) -> list[str]:
    item = _service_plan_text(item_name, "本包设备")
    place = _service_plan_text(delivery_place, "采购人指定地点")
    functional = _service_plan_text(functional_notes, f"围绕{item}的关键功能、联动要求和试运行指标组织安装调试。")
    return [
        f"1）场地勘查与条件确认：设备进场前提前核对{place}的电源、接地、网络、温湿度、承重、给排水、排风及净化等条件（如适用），并与采购人确认安装窗口、施工边界和现场配合事项。",
        f"2）开箱点验与设备定位：货物运抵现场后组织卸货、开箱点验、设备定位和部件组装，逐项核对主机、附件、耗材、工具及随机资料，确认无误后再进入通电和联机阶段。",
        f"3）安装调试与场地联动：按照随机技术文件、标准操作规程和采购文件要求完成安装、初始化设置、功能调试、参数校准和联动测试。{functional}",
        "4）试运行与问题闭环：安装调试完成后安排试运行和结果确认，对接口异常、报警提示、配件缺失、环境不匹配等问题建立现场整改清单，落实责任人、完成时限和复测要求，确保设备具备正式交付条件。",
    ]


def _build_service_training_points(
    item_name: str,
    *,
    training_notes: str = "",
) -> list[str]:
    item = _service_plan_text(item_name, "本包设备")
    training = _service_plan_text(training_notes, f"围绕{item}开展分层培训和实操演练。")
    return [
        f"1）培训对象与分层安排：面向操作人员、科室管理人员和院方设备联络人员分别组织培训，确保使用、管理和日常联络三个层面的受训人员均明确职责和操作边界。",
        f"2）培训内容：覆盖{item}的开关机流程、标准操作步骤、参数设置、样本/试剂/附件使用、日常清洁保养、常见告警识别、简单故障排查和安全注意事项。",
        "3）培训方式与记录：采用现场讲解、上机演示、实操跟训和问答复盘相结合的方式实施，培训完成后形成培训签到、培训课件、培训记录和必要的考核确认材料。",
        f"4）效果确认与持续支持：以现场演示、独立操作、答疑复核等方式确认培训效果，必要时安排二次培训或补充交底。{training}",
    ]


def _build_service_acceptance_points(
    item_name: str,
    *,
    acceptance_notes: str = "",
    support_digest: str = "",
) -> list[str]:
    item = _service_plan_text(item_name, "本包设备")
    acceptance = _service_plan_text(acceptance_notes, f"按采购文件、投标响应、合同约定以及国家和行业标准对{item}进行验收。")
    docs = _service_plan_text(
        support_digest,
        "产品合格证、装箱单、说明书、配置清单、出厂检验资料及注册/备案资料（如适用）",
    )
    return [
        f"1）验收依据与流程：{acceptance} 验收按到货验收、安装验收、功能/性能验收和试运行确认等步骤推进，确保实物、资料和响应承诺一致。",
        f"2）资料移交：同步移交并说明{docs}，保证资料版本完整、内容可核、与实物对应，便于采购人留档和后续管理。",
        f"3）技术核验与试运行：围绕{item}的配置、参数、功能、接口联动和运行稳定性逐项核验，必要时配合采购人开展现场测试、结果记录和复核确认。",
        "4）整改闭环与正式交付：对验收中发现的问题形成书面清单，明确整改措施、责任人和完成时限；整改完成后再次确认，达到交付条件后办理正式签收和交付手续。",
    ]


def _build_service_after_sales_points(
    item_name: str,
    *,
    spec_digest: str = "",
) -> list[str]:
    item = _service_plan_text(item_name, "本包设备")
    specs = _service_plan_text(spec_digest, "本项目所投配置、随机附件和配套资料")
    return [
        f"1）服务组织与报修渠道：围绕{item}建立固定服务联系人、电话/邮件等报修渠道和问题跟踪台账；如采购文件对响应时限有明确要求，我方完全按其执行，未明确事项按“先远程诊断、后现场处理、全过程回访闭环”的原则实施。",
        f"2）质保期内服务：在质保期内对设备本体及合同约定范围内的配件、附件提供维修维护、技术咨询、故障排查和必要的更换支持，保证{specs}持续满足正常使用要求。",
        f"3）巡检保养与备件保障：结合{item}使用频率制定预防性维护计划，按需开展巡检、校准、清洁保养和运行状态检查；同步建立常用备件和关键耗材保障机制，减少停机风险。",
        "4）远程支持与升级服务：在采购文件和厂家政策允许范围内提供远程技术支持、使用指导、软件参数优化、版本升级建议及安全使用提醒，确保用户持续掌握设备运行状态。",
        "5）质保期外延续服务：质保期届满后继续提供有偿维保、配件供应、技术咨询和升级支持，服务标准保持连续一致，不因质保期结束中断正常使用保障。",
    ]


def _build_service_quality_points(
    item_name: str,
    *,
    spec_digest: str = "",
    support_digest: str = "",
) -> list[str]:
    item = _service_plan_text(item_name, "本包设备")
    specs = _service_plan_text(spec_digest, "采购文件技术要求和投标响应配置")
    docs = _service_plan_text(support_digest, "随机文件和验收资料")
    return [
        f"1）质量保证管理体系：围绕{item}建立项目负责人总负责、商务物流协同、技术工程师分工实施的质量管理机制，对供货、安装、培训、验收、维保全过程实施责任到人。",
        f"2）关键节点复核：对备货、出库、包装、到货、安装、调试和试运行等节点设置复核要求，确保{specs}与实际交付一致，避免错发、漏发和错误安装。",
        f"3）记录留存与可追溯管理：对发货通知、物流信息、开箱点验、安装调试、培训签到、验收记录及{docs}进行归档留存，保证项目过程可追溯、可复核。",
        "4）异常处置与纠正预防：发现质量异常、功能偏差或资料不一致时，立即启动隔离、原因分析、纠正处理和复核确认机制，必要时安排补发、更换或现场整改，防止同类问题重复发生。",
    ]


def _renumber_numbered_points(points: list[str], start: int = 1) -> list[str]:
    result: list[str] = []
    for idx, point in enumerate(points, start=start):
        body = re.sub(r"^\d+[）)]\s*", "", _clean_text(point))
        result.append(f"{idx}）{body}")
    return result


def _build_small_enterprise_declaration_template(tender, packages: list[ProcurementPackage] | None = None) -> str:
    """返回中小企业声明函（货物）模板。"""
    goods = _goods_names_text(packages)
    purchaser = _purchaser_text(tender)
    project_name = _project_name_text(tender)
    rows = []
    for idx, pkg in enumerate(packages or [], start=1):
        rows.append(
            f"{idx}. {getattr(pkg, 'item_name', '') or '【待填写：标的名称】'}，属于【待填写：采购文件明确的所属行业】行业；"
            "制造商为【待填写：企业名称】；从业人员【待填写】人，营业收入为【待填写】万元，资产总额为【待填写】万元，"
            "属于【待填写：中型企业/小型企业/微型企业】。"
        )
    if not rows:
        rows.append(
            "1. 【待填写：标的名称】，属于【待填写：采购文件明确的所属行业】行业；制造商为【待填写：企业名称】；"
            "从业人员【待填写】人，营业收入为【待填写】万元，资产总额为【待填写】万元，"
            "属于【待填写：中型企业/小型企业/微型企业】。"
        )

    details = "\n".join(rows)
    return f"""
中小企业声明函（货物）

本公司（联合体）郑重声明，根据《政府采购促进中小企业发展管理办法》（财库〔2020〕46号）的规定，本公司（联合体）参加 {purchaser} 的 {project_name} 采购活动，提供的货物全部由符合政策要求的中小企业制造。相关企业（含联合体中的中小企业、签订分包意向协议的中小企业）的具体情况如下：

{details}

以上企业，不属于大企业的分支机构，不存在控股股东为大企业的情形，也不存在与大企业的负责人为同一人的情形。
本企业对上述声明内容的真实性负责。如有虚假，将依法承担相应责任。

填写说明：
1. 本项目为货物采购时，保留本《中小企业声明函（货物）》正文；如本项目不适用，请在正式稿按采购文件要求删除或注明“本项不适用”。
2. 从业人员、营业收入、资产总额填报上一年度数据；无上一年度数据的新成立企业可不填报。
3. 如存在多个标的，可按上述格式逐项续写，不得只保留标题。

企业名称（盖章）：【待填写：投标人名称】
日期：【待填写：年 月 日】
对应货物：{goods}
""".strip()


def _build_disabled_unit_declaration_template(tender, packages: list[ProcurementPackage] | None = None) -> str:
    """返回残疾人福利性单位声明函模板。"""
    goods = _goods_names_text(packages)
    purchaser = _purchaser_text(tender)
    project_name = _project_name_text(tender)
    return f"""
残疾人福利性单位声明函

本单位郑重声明，根据《财政部 民政部 中国残疾人联合会关于促进残疾人就业政府采购政策的通知》（财库〔2017〕141号）的规定，本单位为符合条件的残疾人福利性单位，且本单位参加 {purchaser} 的 {project_name} 采购活动，提供本单位制造的货物（或由本单位承担的工程、提供的服务），或者提供其他残疾人福利性单位制造的货物（不包括使用非残疾人福利性单位注册商标的货物）。

如本单位不属于残疾人福利性单位，请在正式稿按采购文件要求删除本页或注明“本项不适用”；如属于，请同步附与声明内容一致的证明材料。

本单位对上述声明的真实性负责。如有虚假，将依法承担相应责任。

残疾人福利性单位（盖章）：【待填写：投标人名称】
法定代表人或授权代表：【待填写】
日期：【待填写：年 月 日】
对应货物：{goods}
""".strip()


def _build_affiliated_units_statement_template(tender) -> str:
    """返回投标人关联单位说明模板。"""
    project_name = _project_name_text(tender)
    return f"""
投标人关联单位的说明

为参加 {project_name} 投标/响应活动，现就与本单位存在关联关系的单位说明如下：

1. 与投标人单位负责人为同一人的其他单位：
【待填写：无；如有请填写单位名称、统一社会信用代码及关系说明】

2. 与投标人存在直接控股、管理关系的其他单位：
【待填写：无；如有请填写单位名称、统一社会信用代码及关系说明】

3. 如经核查不存在上述情形，请直接填写“无”；如存在，请如实逐项列明，不得遗漏。

供应商全称（公章）：【待填写：投标人名称】
法定代表人或授权代表：【待填写】
日期：【待填写：年 月 日】
""".strip()


def _build_manufacturer_authorization_template(tender, packages: list[ProcurementPackage] | None = None) -> str:
    """返回制造商授权书模板。"""
    purchaser = _purchaser_text(tender)
    project_name = _project_name_text(tender)
    project_no = _project_number_text(tender)
    goods = _goods_names_text(packages)
    return f"""
制造商授权书

致：{purchaser}

作为【待填写：制造商名称】，现授权【待填写：投标人名称】作为我方就 {project_name}（项目编号：{project_no}）的合法投标人与供货服务实施主体，代表我方参加与 {goods} 相关的投标、供货、安装调试、验收配合、培训及售后服务等工作。

授权范围包括但不限于：
1. 以授权投标人名义参与本项目投标、澄清、答疑及合同洽谈；
2. 按投标承诺及合同约定供应授权产品，并提供原厂或制造商认可的安装调试、培训和售后服务；
3. 在项目实施及质保服务期间，提供必要的技术支持、备件供应和质量保障。

我方承诺：
1. 本授权真实、合法、有效，不存在重复冲突授权；
2. 授权产品来源合法，质量符合国家及行业规范要求；
3. 如项目中标，将配合授权投标人完成本项目履约和售后服务工作。

授权产品：{goods}
制造商名称（盖章）：【待填写：制造商名称】
法定代表人或授权代表：【待填写】
日期：【待填写：年 月 日】
""".strip()


def _build_service_fee_commitment_template(tender) -> str:
    """返回招标代理服务费承诺模板。"""
    agency = _agency_text(tender)
    project_name = _project_name_text(tender)
    project_no = _project_number_text(tender)
    return f"""
招标代理服务费承诺

致：{agency}

如我方在 {project_name}（项目编号：{project_no}）项目中中标/成交，我方承诺在收到中标（成交）通知书后，严格按照招标文件、采购文件及相关约定的收费标准和支付时限，向贵公司一次性足额支付招标代理服务费，并配合完成发票开具和财务对接工作。

中标服务费发票开具方式：请在下列两种方式中二选一保留，其余选项删除。

① 增值税专用发票
公司名称：【待填写】
公司税号：【待填写】
公司地址：【待填写】
公司电话：【待填写】
开户行名称：【待填写】
开户行账号：【待填写】

② 增值税普通发票
公司名称：【待填写】
公司税号：【待填写】

我方保证所提供的开票信息真实、准确、完整。如因信息有误导致发票无法开具、无法认证抵扣或无法入账，由此产生的一切后果由我方自行承担。

承诺方名称（盖章）：【待填写：投标人名称】
法定代表人或授权代表：【待填写】
地址：【待填写：公司注册地址】
电话：【待填写：联系电话】
邮箱：【待填写】
日期：【待填写：年 月 日】
""".strip()


def _build_hlj_supplier_qualification_commitment_template() -> str:
    """返回黑龙江省政府采购供应商资格承诺函的完整可编辑模板。"""
    return """
黑龙江省政府采购供应商资格承诺函

我方作为政府采购供应商，类型为：▢企业 ▢事业单位 ▢社会团体 ▢非企业专业服务机构 ▢个体工商户 ▢自然人（请据实在对应选项中勾选），现郑重承诺如下：

一、承诺具有独立承担民事责任的能力。
（一）供应商类型为企业的，承诺通过合法渠道可查证的信息为：
1. “类型”为“有限责任公司”“股份有限公司”“股份合作制”“集体所有制”“联营”“合伙企业”“其他”等法人企业或合伙企业。
2. “登记状态”为“存续（在营、开业、在册）”。
3. “经营期限”不早于投标截止日期，或长期有效。
（二）供应商类型为事业单位或团体组织的，承诺通过合法渠道可查证的信息为：
1. “类型”为“事业单位”或“社会团体”。
2. “事业单位法人证书或社会团体法人登记证书有效期”不早于投标截止日期。
（三）供应商类型为非企业专业服务机构的，承诺通过合法渠道可查证“执业状态”为“正常”。
（四）供应商类型为自然人的，承诺满足《中华人民共和国民法典》第二章、第六章、第八章等相关条款规定，可独立承担民事责任。

二、承诺具有良好的商业信誉和健全的财务会计制度。
承诺通过合法渠道可查证的信息为：
（一）未被列入失信被执行人。
（二）未被列入税收违法黑名单。

三、承诺具有履行合同所必需的设备和专业技术能力。
承诺按照采购文件要求可提供相关设备和人员清单，以及辅助证明材料。

四、承诺有依法缴纳税收的良好记录。
承诺通过合法渠道可查证的信息为：
（一）不存在欠税信息。
（二）不存在重大税收违法。
（三）不属于纳税“非正常户”（供应商类型为自然人的不适用本条）。

五、承诺有依法缴纳社会保障资金的良好记录。
在承诺函中以附件形式提供至少开标前三个月依法缴纳社会保障资金的证明材料，其中基本养老保险、基本医疗保险（含生育保险）、工伤保险、失业保险均须依法缴纳。

六、承诺参加本次政府采购活动前三年内，在经营活动中没有重大违法记录（处罚期限已经届满的视同没有重大违法记录）。
供应商需承诺通过合法渠道可查证的信息为：
（一）在投标截止日期前三年内未因违法经营受到刑事处罚。
（二）在投标截止日期前三年内未因违法经营受到县级以上行政机关作出的较大金额罚款（二百万元以上）的行政处罚。
（三）在投标截止日期前三年内未因违法经营受到县级以上行政机关作出的责令停产停业、吊销许可证或者执照等行政处罚。

七、承诺参加本次政府采购活动不存在下列情形。
（一）单位负责人为同一人或者存在直接控股、管理关系的不同供应商，不得参加同一合同项下的政府采购活动。除单一来源采购项目外，为采购项目提供整体设计、规范编制或者项目管理、监理、检测等服务的供应商，不得再参加该采购项目的其他采购活动。
（二）承诺通过合法渠道可查证未被列入失信被执行人名单、重大税收违法案件当事人名单、政府采购严重违法失信行为记录名单。

八、承诺通过下列合法渠道，可查证在投标截止日期前一至七款承诺信息真实有效：
（一）国家企业信用信息公示系统（https://www.gsxt.gov.cn）；
（二）中国执行信息公开网（http://zxgk.court.gov.cn）；
（三）中国裁判文书网（https://wenshu.court.gov.cn）；
（四）信用中国（https://www.creditchina.gov.cn）；
（五）中国政府采购网（https://www.ccgp.gov.cn）；
（六）其他具备法律效力的合法渠道。

我方对上述承诺事项的真实性负责，授权并配合采购人所在同级财政部门及其委托机构，对上述承诺事项进行查证。如不属实，属于供应商提供虚假材料谋取中标、成交的情形，按照《中华人民共和国政府采购法》第七十七条第一款的规定，接受相应行政处罚；有违法所得的，并处没收违法所得；情节严重的，由市场监督管理部门吊销营业执照；构成犯罪的，依法追究刑事责任。

附件：缴纳社会保障资金的证明材料清单
一、社保经办机构出具的本单位职工社会保障资金缴纳证明。
（一）基本养老保险缴纳证明或基本养老保险缴费清单。
（二）基本医疗保险及生育保险缴纳证明或缴费清单。
（三）工伤保险缴纳证明或缴费清单。
（四）失业保险缴纳证明或缴费清单。
二、新成立的企业或在法规范围内不需提供相关证明的机构，应另附书面说明，写明成立时间、适用依据及不能提供对应证明材料的原因，并附营业执照、主管部门说明或其他佐证材料。

承诺人（供应商或自然人CA签章）：【待填写：投标人名称】
日期：【待填写：年 月 日】
""".strip()


def _normalize_hlj_supplier_qualification_template(text: str) -> str:
    """标准化黑龙江资格承诺函文本；抽取失败时回退到完整模板。"""
    body = "\n".join(line.strip() for line in (text or "").splitlines() if line.strip())
    compact = _clean_text(body)
    required_tokens = (
        "黑龙江省政府采购供应商资格承诺函",
        "承诺具有独立承担民事责任的能力",
        "承诺有依法缴纳社会保障资金的良好记录",
        "附件：缴纳社会保障资金的证明材料清单",
    )
    if not compact:
        return _build_hlj_supplier_qualification_commitment_template()
    # 黑龙江资格承诺函属于全省通用模板，优先使用标准化正文，避免 OCR 粘连/重复句污染底稿。
    if "黑龙江省政府采购供应商资格承诺函" in compact:
        return _build_hlj_supplier_qualification_commitment_template()
    if compact.count("请按招标文件原格式填写本节内容") >= 2:
        return _build_hlj_supplier_qualification_commitment_template()
    if sum(1 for token in required_tokens if token in compact) < 3:
        return _build_hlj_supplier_qualification_commitment_template()
    return body

def _extract_review_block(tender_raw: str, title_keywords: list[str], stop_keywords: list[str] | None = None) -> str:
    """提取评审块。"""
    stop_keywords = stop_keywords or [
        "响应文件格式", "合同包", "采购包", "报价", "技术参数", "商务要求", "采购需求", "资格承诺函",
    ]
    for key in title_keywords:
        pat = re.compile(
            rf"{re.escape(key)}[：:]?(.*?)(?:(?:{'|'.join(map(re.escape, stop_keywords))})|$)",
            re.S,
        )
        m = pat.search(tender_raw)
        if m:
            body = re.sub(r"-第\d+页-", "", m.group(1) or "")
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            if body:
                return body
    return ""


def _extract_review_rows_from_block(block: str) -> list[str]:
    """提取文本块中的评审行。"""
    if not block:
        return []

    lines = [line.strip() for line in block.splitlines() if line.strip()]
    merged: list[str] = []

    for line in lines:
        if re.match(
            r"^(?:\d+[、.）)]|[（(]?\d+[）)]|[一二三四五六七八九十]+[、.]|★|※|评审因素|评分标准|评审项目)",
            line,
        ):
            merged.append(line)
        else:
            if merged:
                merged[-1] += " " + line

    cleaned: list[str] = []
    for item in merged:
        s = " ".join(item.split())
        if len(s) < 4:
            continue
        if s in {"评审标准", "评分标准", "详细评审", "资格审查", "符合性审查"}:
            continue
        cleaned.append(s.replace("|", "/"))

    return cleaned


def _clean_text(value) -> str:
    """清理文本。"""
    text = str(value or "").replace("|", "/")
    text = re.sub(r"[\u200b\ufeff]+", "", text)
    text = re.sub(r"[-—–]?\s*第\s*\d+\s*页\s*[-—–]?", " ", text)
    text = re.sub(r"[；;]{2,}", "；", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    """返回表格。"""
    aligns = ["---:"] + ["---"] * (len(headers) - 1)
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(aligns) + " |",
    ]
    for row in rows:
        fixed = [_clean_text(x) for x in row]
        if len(fixed) < len(headers):
            fixed += [""] * (len(headers) - len(fixed))
        elif len(fixed) > len(headers):
            fixed = fixed[: len(headers) - 1] + [" / ".join(fixed[len(headers) - 1 :])]
        lines.append("| " + " | ".join(fixed) + " |")
    return "\n".join(lines)


def _row_to_cells(row) -> dict[str, str]:
    """把模板行对象转换成统一的单元格字典。"""
    cells = {str(k): _clean_text(v) for k, v in (getattr(row, "cells", {}) or {}).items()}
    source_text = _clean_text(getattr(row, "source_text", ""))
    package_id = _clean_text(getattr(row, "package_id", ""))
    if source_text:
        cells["_source_text"] = source_text
    if package_id:
        cells["_package_id"] = package_id
    return cells


def _pick_template_rows(table, pkg=None) -> list[dict[str, str]]:
    """挑选模板行。"""
    raw_rows = list(getattr(table, "rows", []) or [])
    if not raw_rows:
        return []

    normalized = [_row_to_cells(row) for row in raw_rows]
    if pkg is None:
        return normalized

    pkg_id = str(getattr(pkg, "package_id", "") or "").strip()
    item_name = _clean_text(getattr(pkg, "item_name", ""))

    picked: list[dict[str, str]] = []
    saw_pkg_hint = False

    for cells in normalized:
        haystack = " ".join(v for k, v in cells.items() if not k.startswith("_"))
        row_pkg = cells.get("_package_id", "")

        if row_pkg:
            saw_pkg_hint = True
            if row_pkg == pkg_id:
                picked.append(cells)
                continue

        if any(marker and marker in haystack for marker in (f"合同包{pkg_id}", f"包{pkg_id}", item_name)):
            saw_pkg_hint = True
            picked.append(cells)

    if picked:
        return picked

    # 如果表本身没有任何包号提示，默认整张表对所有包通用
    if not saw_pkg_hint:
        return normalized

    return []


def _extract_labeled_block(text: str, labels: list[str], stop_labels: list[str]) -> str:
    """提取labeled文本块。"""
    text = text or ""
    stop_pat = "|".join(map(re.escape, stop_labels))
    for label in labels:
        m = re.search(
            rf"{re.escape(label)}[：:]?\s*(.*?)(?=(?:{stop_pat})[：:]?|$)",
            text,
            re.S,
        )
        if m and (m.group(1) or "").strip():
            return m.group(1).strip()
    return ""


def _parse_named_rows(block: str, keys: list[str]) -> list[tuple[str, str]]:
    """解析named行。"""
    text = "\n".join(_clean_text(x) for x in (block or "").splitlines() if _clean_text(x))
    if not text:
        return []

    key_pat = "|".join(sorted((re.escape(k) for k in keys), key=len, reverse=True))
    rows: list[tuple[str, str]] = []

    for m in re.finditer(
        rf"({key_pat})\s*(.*?)(?=(?:{key_pat}|合同包\s*\d+|表[一二三四五六七八九十]+|第[五六七八九十]章|$))",
        text,
        re.S,
    ):
        key = _clean_text(m.group(1))
        value = _clean_text(m.group(2))
        if value:
            rows.append((key, value))
    return rows


def _dedupe_named_rows(
    rows: list[tuple[str, str]],
    normalizer=None,
) -> list[tuple[str, str]]:
    """去重named行。"""
    seen: set[str] = set()
    result: list[tuple[str, str]] = []

    for key, value in rows:
        norm_key = normalizer(key) if normalizer else key
        if norm_key in seen:
            continue
        seen.add(norm_key)
        result.append((norm_key, value))
    return result


def _normalize_detailed_review_key(key: str) -> str:
    """归一化详细评审键。"""
    key = _clean_text(key)
    key = key.replace("商务部分 ", "")
    key = key.replace("投标报价 ", "")
    return key


def _is_valid_invalid_item(text: str) -> bool:
    """判断valid无效项。"""
    s = _clean_text(text)
    if not s or len(s) < 6:
        return False

    bad_markers = [
        "主要商务要求",
        "技术标准与要求",
        "附表一",
        "分项预算",
        "参数性质",
        "设备名称",
        "手术用头架技术参数与性能要求",
        "X射线血液辐照仪技术参数与性能要求",
    ]
    if any(x in s for x in bad_markers):
        return False

    good_markers = [
        "无效",
        "废标",
        "未按",
        "不满足",
        "虚假材料",
        "串通投标",
        "签字",
        "盖章",
        "报价",
        "资格性审查",
        "符合性审查",
        "授权书",
        "解密",
        "签章确认",
        "重大违法记录",
    ]
    return any(x in s for x in good_markers)


def _normalize_number_text(value) -> str:
    """归一化number文本。"""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
        return str(f)
    except Exception:
        return s


def _normalize_dense_text(text: str) -> str:
    """归一化紧凑文本，便于匹配。"""
    text = re.sub(r"[-—–]?\s*第\s*\d+\s*页\s*[-—–]?", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _extract_front_matter_scope(tender_raw: str) -> str:
    """提取前置正文前部范围。"""
    text = tender_raw or ""
    if not text:
        return ""

    stop_patterns = [
        r"第二章\s*采购人需求",
        r"第二章\s*采购需求",
        r"第五章\s*采购需求",
        r"第五章\s*货物需求",
        r"第五章\s*用户需求",
    ]
    stop_pos = None
    for pat in stop_patterns:
        match = re.search(pat, text)
        if match:
            if stop_pos is None or match.start() < stop_pos:
                stop_pos = match.start()
    if stop_pos is not None:
        return text[:stop_pos]
    return text[:12000]


def _extract_package_summary_rows(tender_raw: str) -> list[dict]:
    """
    从首页/采购邀请中的“谈判/磋商/招标内容”表抽包号、名称、数量、预算。
    常见形式：
    1 X射线血液辐照设备 1 详见采购文件 2,145,000.00
    """
    rows: list[dict] = []
    scope = _normalize_dense_text(_extract_front_matter_scope(tender_raw))
    if not scope:
        return rows

    patterns = [
        re.compile(
            r"(?P<pkg>\d+)\s+"
            r"(?P<name>.+?)\s+"
            r"(?P<qty>\d+(?:\.\d+)?)\s+"
            r"详见采购文件\s+"
            r"(?P<budget>[0-9,]+(?:\.\d+)?)"
        ),
        re.compile(
            r"(?P<pkg>\d+)\s+"
            r"(?P<name>.+?)\s+"
            r"(?P<qty>\d+(?:\.\d+)?)\s+"
            r"(?P<budget>[0-9,]+(?:\.\d+)?)\s+"
            r"(?P<delivery>(?:合同签订后|签订合同后)[^。；]*?(?:交货|送达指定地点))\s+"
            r"(?P<place>甲方指定地点|采购人指定地点|招标人指定地点|[^。；]+?地点)"
        ),
    ]

    seen: set[str] = set()
    for pattern in patterns:
        for m in pattern.finditer(scope):
            package_id = m.group("pkg").strip()
            if package_id in seen:
                continue
            seen.add(package_id)
            rows.append(
                {
                    "package_id": package_id,
                    "item_name": " ".join(m.group("name").split()),
                    "quantity": m.group("qty").strip(),
                    "budget": m.group("budget").replace(",", "").strip(),
                    "delivery_time": " ".join((m.groupdict().get("delivery") or "").split()),
                    "delivery_place": " ".join((m.groupdict().get("place") or "").split()),
                }
            )
    return rows


def _find_summary_row(tender_raw: str, package_id: str) -> dict | None:
    """查找汇总行。"""
    for row in _extract_package_summary_rows(tender_raw):
        if row["package_id"] == str(package_id):
            return row
    return None


def _extract_package_quantity(pkg, tender_raw: str) -> str:
    """
    数量优先级：
    1. 首页‘磋商内容’表中的包数量
    2. 包对象 quantity
    3. 再兜底
    """
    row = _find_summary_row(tender_raw, pkg.package_id)
    if row and row.get("quantity"):
        return str(row["quantity"]).strip()

    q = getattr(pkg, "quantity", None)
    if q not in (None, ""):
        return str(q).strip()

    return "【待填写：数量】"


_COMMERCIAL_TRUNCATE_TOKENS = (
    "投标有效期", "付款方式", "付款条件", "验收要求", "验收标准",
    "履约保证金", "质保期", "质保要求", "售后服务", "违约责任",
    "交货地点", "交货期限", "投标报价", "评分标准", "评分办法",
    "保险要求", "包装要求", "运输要求", "合同条款", "商务条款",
    "标的提供的地点", "标的提供的时间", "采购项目（标的）交付的地点",
    "采购项目（标的）交付的时间",
)


def _truncate_commercial_tail(text: str, *, keep_delivery_place: bool = False) -> str:
    """截断提取文本中混入的商务条款尾巴。"""
    if not text:
        return text
    truncate_tokens = [t for t in _COMMERCIAL_TRUNCATE_TOKENS
                       if not (keep_delivery_place and t in ("交货地点", "标的提供的地点", "采购项目（标的）交付的地点"))]
    earliest = len(text)
    for token in truncate_tokens:
        idx = text.find(token)
        if idx > 0 and idx < earliest:
            earliest = idx
    result = text[:earliest].rstrip(" ；;，,：:、\t")
    return result if result else text


def _extract_delivery_time(pkg, tender_raw: str) -> str:
    """提取交付时间。"""
    row = _find_summary_row(tender_raw, pkg.package_id)
    if row and row.get("delivery_time"):
        return _truncate_commercial_tail(row["delivery_time"])

    front_scope = _normalize_dense_text(_extract_front_matter_scope(tender_raw))
    if front_scope:
        front_patterns = [
            rf"交货期[：:]?[\s\S]{{0,400}}?合同包\s*{re.escape(str(pkg.package_id))}\s*[（(][^）)]*[）)]\s*[：:]?\s*((?:合同签订后|签订合同后)[^。；]*?(?:交货|送达指定地点))",
            rf"交货期限[：:]?[\s\S]{{0,400}}?合同包\s*{re.escape(str(pkg.package_id))}\s*[（(][^）)]*[）)]\s*[：:]?\s*((?:合同签订后|签订合同后)[^。；]*?(?:交货|送达指定地点))",
        ]
        for pat in front_patterns:
            match = re.search(pat, front_scope)
            if match:
                return _truncate_commercial_tail(" ".join(match.group(1).split()))

    block = _find_package_block(tender_raw, pkg.package_id)
    if block:
        patterns = [
            r"采购项目（标的）交付的时间\s*[：:]\s*([^\n]+)",
            r"采购项目\(标的\)交付的时间\s*[：:]\s*([^\n]+)",
            r"交付的时间\s*[：:]\s*([^\n]+)",
            r"标的提供的时间\s*([^\n]+)",
            r"合同履行期限\s*([^\n]+)",
            r"交货期[：:]\s*([^\n]+)",
        ]
        for pat in patterns:
            m = re.search(pat, block)
            if m:
                return _truncate_commercial_tail(" ".join(m.group(1).split()))

    return "按采购文件要求"


def _extract_delivery_place(pkg, tender_raw: str) -> str:
    """提取交付地点。"""
    row = _find_summary_row(tender_raw, pkg.package_id)
    if row and row.get("delivery_place"):
        return _truncate_commercial_tail(row["delivery_place"], keep_delivery_place=True)

    front_scope = _normalize_dense_text(_extract_front_matter_scope(tender_raw))
    if front_scope:
        front_patterns = [
            rf"交货地点[：:]?[\s\S]{{0,400}}?合同包\s*{re.escape(str(pkg.package_id))}\s*[（(][^）)]*[）)]\s*[：:]?\s*(甲方指定地点|采购人指定地点|招标人指定地点|[^。；]+?地点)",
            rf"交货地址[：:]?[\s\S]{{0,400}}?合同包\s*{re.escape(str(pkg.package_id))}\s*[（(][^）)]*[）)]\s*[：:]?\s*(甲方指定地点|采购人指定地点|招标人指定地点|[^。；]+?地点)",
        ]
        for pat in front_patterns:
            match = re.search(pat, front_scope)
            if match:
                return _truncate_commercial_tail(" ".join(match.group(1).split()), keep_delivery_place=True)

    block = _find_package_block(tender_raw, pkg.package_id)
    if block:
        patterns = [
            r"采购项目（标的）交付的地点\s*[：:]\s*([^\n]+)",
            r"采购项目\(标的\)交付的地点\s*[：:]\s*([^\n]+)",
            r"交付的地点\s*[：:]\s*([^\n]+)",
            r"标的提供的地点\s*([^\n]+)",
            r"交货地点[：:]\s*([^\n]+)",
            r"供货地点[：:]\s*([^\n]+)",
        ]
        for pat in patterns:
            m = re.search(pat, block)
            if m:
                return _truncate_commercial_tail(" ".join(m.group(1).split()), keep_delivery_place=True)

    return "甲方指定地点"


def _extract_package_budget(pkg, tender_raw: str) -> str:
    """提取包件预算。"""
    row = _find_summary_row(tender_raw, pkg.package_id)
    if row and row.get("budget"):
        try:
            return f"{float(row['budget']):,.2f}"
        except Exception:
            return str(row["budget"])
    value = getattr(pkg, "budget_amount", None) or getattr(pkg, "budget", None)
    if value not in (None, ""):
        try:
            return f"{float(value):,.2f}"
        except Exception:
            return str(value)
    return "【待填写：预算金额】"


def _budget_text(pkg: ProcurementPackage) -> str:
    """返回文本。"""
    for name in ("budget_amount", "budget", "package_budget", "estimated_amount", "amount"):
        value = getattr(pkg, name, None)
        if value not in (None, ""):
            try:
                return f"{float(value):,.2f}"
            except Exception:
                return str(value)
    return "【待填写：预算金额】"


def _extract_requirements_chapter(tender_raw: str) -> str:
    """提取需求chapter。"""
    text = tender_raw or ""
    patterns = [
        r"第五章\s*采购需求(.*?)(?=第六章\s*投标文件格式|第六章\s*响应文件格式|第六章|$)",
        r"第五章\s*货物需求.*?(.*?)(?=第六章\s*投标文件格式|第六章\s*响应文件格式|第六章|$)",
        r"第二章\s*采购人需求(.*?)(?=第三章\s*投标人须知|第三章\s*供应商须知|第三章|第四章|第五章|第六章|$)",
        r"采购需求[：:]?(.*?)(?=第六章\s*投标文件格式|第六章\s*响应文件格式|第六章|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.S)
        if m and (m.group(1) or "").strip():
            return m.group(1).strip()
    return text


def _find_package_block(tender_raw: str, package_id: str) -> str:
    """查找包件文本块。"""
    scope = _extract_requirements_chapter(tender_raw)
    pid = str(package_id).strip()

    start_patterns = [
        rf"合同包\s*{re.escape(pid)}\s*[（(:：]?",
        rf"包\s*{re.escape(pid)}\s*[：:]",
        rf"第\s*{re.escape(pid)}\s*包",
        rf"采购包\s*{re.escape(pid)}\s*[：:]?",
    ]

    starts: list[tuple[int, int]] = []
    for pat in start_patterns:
        for m in re.finditer(pat, scope):
            starts.append((m.start(), m.end()))

    if not starts:
        return scope

    starts.sort(key=lambda x: x[0])
    start, start_end = starts[0]

    next_header_pat = re.compile(
        r"(合同包\s*\d+\s*[（(:：]?|包\s*\d+\s*[：:]|第\s*\d+\s*包|采购包\s*\d+\s*[：:]?|第三章|第四章|第五章|第六章)"
    )
    m_next = next_header_pat.search(scope, start_end)
    end = m_next.start() if m_next else len(scope)

    return scope[start:end]


def _extract_detail_quantity(pkg: ProcurementPackage, tender_raw: str) -> str:
    """提取明细数量。"""
    block = _find_package_block(tender_raw, pkg.package_id)
    if block:
        m = re.search(r"二、数量[：:]\s*([0-9]+(?:\.[0-9]+)?)", block)
        if m:
            return _normalize_number_text(m.group(1))
    return _normalize_number_text(getattr(pkg, "quantity", "")) or "【待填写：数量】"


def _merge_numbered_lines(text: str) -> list[str]:
    """合并numbered行。"""
    items: list[str] = []
    for raw in text.splitlines():
        s = " ".join(raw.strip().split())
        if not s:
            continue
        if re.match(r"^(?:※?\d+[、.]|[一二三四五六七八九十]+、|设备名称：|[一二三四五六七八九十]+、产地：|[一二三四五六七八九十]+、数量：)", s):
            items.append(s)
        else:
            if items:
                items[-1] += (" " if not items[-1].endswith(("：", ":")) else "") + s
            else:
                items.append(s)
    return items


def _extract_tech_points(pkg: ProcurementPackage, tender_raw: str) -> list[str]:
    """提取技术要点。"""
    block = _find_package_block(tender_raw, pkg.package_id)
    if not block:
        return []

    m = re.search(
        r"(设备名称：.*?)(?:四、装箱配置单：|四、装箱配置单|五、质保：)",
        block,
        re.S,
    )
    if not m:
        return []

    points = _merge_numbered_lines(m.group(1))
    clean_points: list[str] = []
    for p in points:
        if "说明 打“★”号条款" in p:
            continue
        if p.strip():
            clean_points.append(p.strip())
    return clean_points


def _extract_service_points(pkg: ProcurementPackage, tender_raw: str) -> list[str]:
    """提取服务要点。"""
    block = _find_package_block(tender_raw, pkg.package_id)
    if not block:
        return ["按采购文件售后服务要求执行。"]

    m = re.search(r"六、售后服务要求[：:]?(.*?)(?:说明\s*打[“\"]?★|说明\s*打[“\"]?\*)", block, re.S)
    if not m:
        return ["按采购文件售后服务要求执行。"]

    points = _merge_numbered_lines(m.group(1))
    return [p.strip() for p in points if p.strip()] or ["按采购文件售后服务要求执行。"]


def _build_quote_summary_table(
    tender: TenderDocument,
    packages: list[ProcurementPackage],
    tender_raw: str,
) -> str:
    """构建报价汇总表。"""
    lines = [
        "项目名称：{}".format(tender.project_name),
        "项目编号：{}".format(tender.project_number),
        "| 序号(包号) | 货物名称 | 货物报价价格(元) | 货物市场价格(元) | 交货期 |",
        "|---:|---|---:|---:|---|",
    ]
    for idx, pkg in enumerate(packages, start=1):
        delivery = _extract_delivery_time(pkg.package_id, tender_raw)
        market_price = _budget_text(pkg)
        lines.append(
            f"| {idx}（{pkg.package_id}） | {pkg.item_name} | 【待填写：包{pkg.package_id}报价】 | {market_price} | {delivery} |"
        )
    lines.extend(
        [
            "",
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：年 月 日】",
        ]
    )
    return "\n".join(lines)


def _build_pkg_deviation_table(
    tender: TenderDocument,
    pkg: ProcurementPackage,
    tender_raw: str,
) -> str:
    """构建包件偏离表。"""
    qty = _extract_detail_quantity(pkg, tender_raw)
    tech_points = _extract_tech_points(pkg, tender_raw)

    lines = [
        f"包{pkg.package_id}：{pkg.item_name}",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        f"（第{pkg.package_id}包）",
        "| 序号 | 货物名称 | 品牌型号、产地 | 数量/单位 | 报价(元) | 谈判文件的参数和要求 | 响应文件参数 | 偏离情况 |",
        "|---:|---|---|---|---:|---|---|---|",
    ]

    if not tech_points:
        tech_points = ["【待人工根据采购文件逐条补录技术参数，禁止仅写“响应/完全响应”】"]

    for idx, point in enumerate(tech_points, start=1):
        lines.append(
            f"| {idx} | {pkg.item_name} | 【待填写：品牌/型号/产地】 | {qty}/台 | 【待填写】 | {point.replace('|', '/')} | 【待填写：逐条响应】 | 【待填写：无偏离/正偏离/负偏离】 |"
        )

    lines.extend(
        [
            "",
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：年 月 日】",
        ]
    )
    return "\n".join(lines)


def _build_service_section(
    packages: list[ProcurementPackage],
    tender_raw: str,
) -> str:
    """构建服务章节。"""
    parts: list[str] = []

    for pkg in packages:
        delivery_time = _extract_delivery_time(pkg.package_id, tender_raw)
        delivery_place = "采购人指定地点"
        service_points = _extract_service_points(pkg, tender_raw)

        parts.extend(
            [
                f"### 包{pkg.package_id}：{pkg.item_name}",
                f"交货期：{delivery_time}",
                f"交货地点：{delivery_place}",
                "",
                "#### 1. 供货组织措施",
                "我方将成立本项目专项执行小组，负责备货、发运、到货、安装、调试、培训、验收和售后全过程管理，确保进度可控、责任到人。",
                "",
                "#### 2. 安装调试与培训措施",
                "设备到货后按采购文件要求完成开箱核验、安装调试、功能验证和人员培训，并形成安装调试及培训记录。",
                "",
                "#### 3. 本包售后服务承诺",
            ]
        )

        for p in service_points:
            parts.append(f"- {p}")

        parts.extend(
            [
                "",
                "#### 4. 验收配合措施",
                "按采购文件约定提交合格证、注册证/备案凭证、出厂检验报告、装箱单、说明书等资料，配合采购人完成到货验收、功能配置验收和技术性能指标检测。",
                "",
            ]
        )

    parts.extend(
        [
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：年 月 日】",
        ]
    )
    return "\n".join(parts)


def _extract_anchor_block(text: str, anchor_patterns: list[str], stop_patterns: list[str] | None = None, max_chars: int = 9000) -> str:
    """提取anchor文本块。"""
    if not text:
        return ""
    stop_patterns = stop_patterns or []
    start = -1
    for pat in anchor_patterns:
        m = re.search(pat, text, re.S)
        if m:
            start = m.start()
            break
    if start < 0:
        return ""
    end = min(len(text), start + max_chars)
    tail = text[start:end]
    for pat in stop_patterns:
        m2 = re.search(pat, tail, re.S)
        if m2 and m2.start() > 0:
            tail = tail[:m2.start()]
            break
    return tail.strip()


def _merge_bullet_lines(block: str) -> list[str]:
    """合并bullet行。"""
    if not block:
        return []
    raw_lines = [" ".join(line.strip().split()) for line in block.splitlines() if line and line.strip()]
    merged: list[str] = []
    bullet_pat = re.compile(r"^(?:[（(]?\d+[）)]|\d+[、.]|[一二三四五六七八九十]+[、.]|[①②③④⑤⑥⑦⑧⑨⑩])")
    for line in raw_lines:
        if bullet_pat.match(line):
            merged.append(line)
        else:
            if merged:
                merged[-1] += (" " if not merged[-1].endswith(("：", ":")) else "") + line
            else:
                merged.append(line)
    cleaned: list[str] = []
    for item in merged:
        s = re.sub(r"^(?:[（(]?\d+[）)]|\d+[、.]|[一二三四五六七八九十]+[、.]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", "", item).strip()
        if len(s) < 4:
            continue
        if any(tok in s for tok in ("审查项", "招标文件要求", "响应文件对应内容", "是否满足", "备注")):
            continue
        cleaned.append(s)
    return cleaned


def _split_review_row(text: str) -> tuple[str, str]:
    """切分评审行。"""
    s = text.strip(" ：:")
    for sep in ("：", ":", "——", "--", "-", "，"):
        if sep in s:
            left, right = s.split(sep, 1)
            left = left.strip()
            right = right.strip()
            if 2 <= len(left) <= 24 and right:
                return left, right
    short = s[:18].rstrip("，。；:：")
    return short or "审查项", s


def _build_review_table_markdown(rows: list[tuple[str, str]]) -> str:
    """构建评审表 Markdown。"""
    lines = [
        "| 序号 | 审查项 | 招标文件要求 | 响应文件对应内容 | 是否满足 | 备注 |",
        "|---:|---|---|---|---|---|",
    ]
    for idx, (item_name, requirement) in enumerate(rows, start=1):
        lines.append(
            f"| {idx} | {item_name} | {requirement} | 【待填写：对应材料名称/页码】 | "
            f"【待填写：满足/不满足】 | 【待填写】 |"
        )
    return "\n".join(lines)


def _extract_review_rows_from_tender(tender_raw: str, title_patterns: list[str], fallback_rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """提取招标文件中的评审行。"""
    block = _extract_anchor_block(
        tender_raw,
        anchor_patterns=title_patterns,
        stop_patterns=[r"表[三四五六七八九十]", r"(?:第[一二三四五六七八九十]+章)", r"投标无效", r"响应无效", r"评分标准", r"详细评审"],
        max_chars=5000,
    )
    items = _merge_bullet_lines(block)
    rows: list[tuple[str, str]] = []
    for item in items:
        if any(tok in item for tok in ("未通过", "无效投标", "响应无效")):
            continue
        rows.append(_split_review_row(item))
    return rows or fallback_rows


def _extract_invalid_bid_items(tender_raw: str) -> list[str]:
    """提取废标项。"""
    text = tender_raw or ""
    if not text:
        return []

    def _clean(line: str) -> str:
        """清理清理。"""
        s = re.sub(r"\s+", " ", (line or "")).strip(" \t\r\n|：:;；，,")
        s = re.sub(r"^[（(]?\d+[)）]\s*", "", s)
        s = re.sub(r"^\d+[.、]\s*", "", s)
        s = re.sub(r"^[一二三四五六七八九十]+\s*[、.]\s*", "", s)
        return s.strip()

    def _ok(line: str) -> bool:
        """判断当前文本行是否满足评分项抽取条件。"""
        s = _clean(line)
        if not s or len(s) < 10:
            return False

        bad = [
            "审查表",
            "招标文件要求",
            "响应文件对应内容",
            "评分办法索引",
            "资格性检查索引",
            "符合性检查索引",
            "序号",
            "注：",
            "说明：",
        ]
        if any(x in s for x in bad):
            return False

        keys = [
            "无效投标",
            "投标无效",
            "视为无效",
            "按无效投标处理",
            "按无效处理",
            "被拒绝",
            "不予受理",
            "不予认可",
            "拒绝其投标",
        ]
        return any(k in s for k in keys)

    items: list[str] = []

    # 1) 优先抓真正的规则块
    block_patterns = [
        r"26\.5[\s\S]{0,3000}",
        r"本项目规定的其他无效投标情况[:：]?[\s\S]{0,2200}",
        r"23\.2[\s\S]{0,1000}",
        r"26\.6[\s\S]{0,1000}",
        r"15\.3[\s\S]{0,800}",
        r"16\.1[\s\S]{0,800}",
        r"3\.5[\s\S]{0,800}",
    ]

    blocks: list[str] = []
    for pat in block_patterns:
        m = re.search(pat, text)
        if m:
            blocks.append(m.group(0))

    # 2) 从块里只提编号条款
    enum_pat = re.compile(
        r"(?:^|\n)\s*[（(]?\d+[)）]\s*(.+?)(?=(?:\n\s*[（(]?\d+[)）]\s*)|\Z)",
        re.S
    )

    for blk in blocks:
        for m in enum_pat.finditer(blk):
            s = _clean(m.group(1))
            if _ok(s) and s not in items:
                items.append(s.rstrip("；;。") + "。")

    # 3) 补抓少数没有编号、但非常关键的直接规则句
    direct_patterns = [
        r"未按上述要求提供进口产品逐级授权的投标视为未响应招标文件实质性要求，其投标无效",
        r"凡没有根据投标人须知第\s*15\.1\s*和\s*15\.2\s*条的规定随附投标保证金的投标，将按投标人须知第\s*23\s*条的规定视为无效投标予以拒绝",
        r"投标有效期不满足要求的投标将被视为无效投标而予以拒绝",
        r"投标人存在下列情况之一的，投标无效",
        r"投标人不能证明其报价合理性的，评标委员会应当将其作为无效投标处理",
    ]

    for pat in direct_patterns:
        for m in re.finditer(pat, text):
            s = _clean(m.group(0))
            if _ok(s) and s not in items:
                items.append(s.rstrip("；;。") + "。")

    return items

def _extract_scoring_items(tender, tender_raw: str) -> list[str]:
    """提取scoring项。"""
    block = _extract_anchor_block(
        tender_raw,
        anchor_patterns=[r"详细评审", r"评分标准", r"评审标准"],
        stop_patterns=[r"响应文件格式", r"第[一二三四五六七八九十]+章", r"投标无效", r"响应无效"],
        max_chars=7000,
    )
    items = []
    for item in _merge_bullet_lines(block):
        if any(tok in item for tok in ("资格性审查", "符合性审查", "价格分采用", "未通过", "废标")):
            continue
        if len(item) >= 6 and item not in items:
            items.append(item)
    if items:
        return items
    eval_rules = getattr(tender, "evaluation_criteria", {}) or {}
    fallback: list[str] = []
    for k, v in eval_rules.items():
        fallback.append(f"{k}：{v}")
    return fallback


def _build_detailed_review_section(tender, tender_raw: str) -> str:
    """构建详细评审章节。"""
    items = _extract_scoring_items(tender, tender_raw)
    lines = [
        "| 序号 | 评分项 | 招标文件评分标准 | 响应文件对应内容 | 自评说明 | 证明材料页码 |",
        "|---:|---|---|---|---|---|",
    ]
    if not items:
        items = ["【待补：从采购文件评分标准章节提取详细评审项】"]
    for idx, item in enumerate(items, start=1):
        if "：" in item:
            name, rule = item.split("：", 1)
        elif ":" in item:
            name, rule = item.split(":", 1)
        else:
            name, rule = f"评分项{idx}", item
        lines.append(
            f"| {idx} | {name.strip()} | {rule.strip()} | 【待填写：对应响应内容】 | "
            f"【待填写：自评得分理由】 | 【待填写：页码】 |"
        )
    return "\n".join(lines)
