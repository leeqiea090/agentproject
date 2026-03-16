from __future__ import annotations

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.table_builders as _table_builders
from langchain_openai import ChatOpenAI

from app.schemas import BidDocumentSection, ProcurementPackage, TenderDocument
from app.services.one_click_generator.common import (
    _ADDRESS,
    _AUTHORIZED_REP,
    _COMPANY,
    _LEGAL_REP,
    _PHONE,
    _allow_consortium,
    _fmt_money,
    _has_imported_clues,
    _infer_package_quantity,
    _is_medical_project,
    _normalize_commitment_term,
    _package_scope,
    _safe_text,
    _supplier_commitment_title,
    _today,
)

def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _table_builders,):
    __reexport_all(_module)

del _module
def _build_qualification_license_block(tender: TenderDocument) -> str:
    """构建资格审查资质文本块。"""
    lines = [
        "### 证照/注册文件台账",
        "| 材料项 | 对应主体 | 是否适用 | 建议文件名 | 核对要点 |",
        "|---|---|---|---|---|",
        "| 投标公司营业执照 | 投标人 | 必须 | 01_投标人营业执照.pdf | 核对名称、统一社会信用代码、经营状态 |",
    ]

    if _is_medical_project(tender):
        lines.extend([
            "| 投标公司医疗器械经营许可证/备案凭证 | 投标人 | 如适用 | 02_投标人医疗器械经营许可或备案.pdf | 核对主体名称、许可/备案范围、有效状态 |",
            "| 生产厂家营业执照 | 生产厂家 | 必须 | 03_生产厂家营业执照.pdf | 核对主体名称与授权链、注册证信息一致 |",
            "| 生产厂家医疗器械生产/经营许可文件 | 生产厂家 | 如适用 | 04_生产厂家医疗器械生产或经营许可.pdf | 核对许可范围与产品类别匹配 |",
            "| 投标产品注册证/备案证明 | 产品 | 如适用 | 05_产品注册证或备案凭证.pdf | 核对产品名称、型号规格、注册人、有效期 |",
        ])
    else:
        lines.extend([
            "| 行业资质证书 | 投标人 | 如适用 | 02_行业资质证书.pdf | 核对资质范围与项目内容匹配 |",
            "| 质量管理体系或服务能力证明 | 投标人 | 如适用 | 03_质量体系或服务能力证明.pdf | 核对证书有效期与认证范围 |",
        ])

    lines.append(
        "| 投标产品授权文件 | 投标人/生产厂家 | 如需授权 | 06_授权文件.pdf | 核对授权链、品牌型号、有效期 |"
    )

    if _has_imported_clues(tender):
        lines.append(
            "| 进口产品合法来源与报关资料 | 产品/进口链路 | 如适用 | 07_进口合法来源及报关资料.pdf | 核对报关单、海关信息、供货链路 |"
        )

    return "\n".join(lines)


def _build_review_response_table(
    title: str,
    rows: list[tuple[str, str]],
) -> str:
    """构建评审响应表，作为人工补材料的导航表。"""
    lines = [
        f"## {title}",
        "| 序号 | 审查项 | 招标文件要求 | 响应定位 | 补充说明 |",
        "|---:|---|---|---|---|",
    ]

    # 精确定位到具体章节位置
    location_map = {
        "独立承担民事责任能力": ("第一章第一节/营业执照", "提供营业执照副本复印件并加盖公章"),
        "授权书": ("第一章第二节/授权书", "法定代表人签字并加盖公章"),
        "身份证明": ("第一章第三节/身份证复印件", "法定代表人及授权代表身份证正反面复印件"),
        "投标报价": ("第二章/投标报价一览表", "确保不超预算且唯一报价"),
        "投标文件规范性、符合性": ("全文", "检查签字盖章完整性，目录与正文一致"),
        "主要商务条款": ("第二章/商务条款承诺书", "按招标文件格式填写并签章"),
        "联合体投标": ("第一章/联合体投标声明", "如不组联合体，明确声明独立投标"),
        "技术部分实质性内容": ("第三章/技术偏离及详细配置明细表", "逐条响应所有★号条款，明确品牌型号"),
        "其他要求": ("第一章/围标串标承诺函", "提供承诺函并签章"),
    }

    for idx, (item_name, requirement) in enumerate(rows, 1):
        location, note = location_map.get(item_name, ("【待定位】", "按招标文件要求提供"))
        lines.append(
            f"| {idx} | {item_name} | {requirement} | {location} | {note} |"
        )
    return "\n".join(lines)

def _build_enterprise_declaration_block(tender: TenderDocument, today: str) -> str:
    """构建enterprisedeclaration文本块。"""
    _ = tender
    return f"""## 八、企业类型声明/证明材料
| 选项 | 是否适用 | 处理方式 | 需附材料 |
|---|---|---|---|
| 中小企业 | 【待填写：是/否】 | 如适用，直接替换为采购文件附带的《中小企业声明函》原格式 | 中小企业声明函 |
| 监狱企业 | 【待填写：是/否】 | 如适用，仅保留本项并附证明文件 | 监狱企业证明文件 |
| 残疾人福利性单位 | 【待填写：是/否】 | 如适用，仅保留本项并附声明函/证明材料 | 残疾人福利性单位声明函 |
| 非中小企业 | 【待填写：是/否】 | 如不享受相关政策，仅保留本项简短声明 | 非中小企业声明 |

> 说明：
> 1. 正式稿只能保留一类企业属性，不要把多类声明同时保留。
> 2. 如采购文件附有固定格式/附表，优先直接使用采购文件原格式，不要自行改写。
> 3. 人工审核时，先判定企业属性，再替换或删除本块。

企业名称（盖章）：{_COMPANY}  
法定代表人或授权代表：{_AUTHORIZED_REP}  
日期：{today}"""

def _build_social_insurance_checklist() -> str:
    """构建社保保险checklist。"""
    return "\n".join([
        "### 社保/保险证明清单",
        "| 材料项 | 建议期间 | 建议文件名 | 备注 |",
        "|---|---|---|---|",
        "| 基本养老保险缴纳证明 | 最近1-3个月，以采购文件为准 | 01_基本养老保险缴纳证明.pdf | 主体应与投标人名称一致 |",
        "| 基本医疗保险及生育保险缴纳证明 | 最近1-3个月，以采购文件为准 | 02_基本医疗及生育保险缴纳证明.pdf | 主体应与投标人名称一致 |",
        "| 工伤保险缴纳证明 | 最近1-3个月，以采购文件为准 | 03_工伤保险缴纳证明.pdf | 主体应与投标人名称一致 |",
        "| 失业保险缴纳证明 | 最近1-3个月，以采购文件为准 | 04_失业保险缴纳证明.pdf | 主体应与投标人名称一致 |",
    ])

def _gen_qualification_review_section() -> BidDocumentSection:
    """返回资格审查章节。"""
    rows = [
        ("独立承担民事责任能力", "提供营业执照或对应主体资格证明文件"),
        ("授权书", "法定代表人/单位负责人授权书签字并加盖公章"),
        ("身份证明", "法定代表人/单位负责人及授权代表身份证正反面复印件"),
    ]
    content = _build_review_response_table("资格性审查响应对照表", rows)
    return BidDocumentSection(section_title="资格性审查响应对照表", content=content)

def _gen_compliance_review_section() -> BidDocumentSection:
    """返回符合性审查章节。"""
    rows = [
        ("投标报价", "只能有一个有效报价且不超过预算/最高限价"),
        ("投标文件规范性、符合性", "签署、盖章、格式、文字、目录等符合要求"),
        ("主要商务条款", "提供满足主要商务条款的承诺书"),
        ("联合体投标", "符合联合体相关规定"),
        ("技术部分实质性内容", "明确品牌并满足全部实质性要求"),
        ("其他要求", "围标、串标和法律法规规定的其它无效投标条款"),
    ]
    content = _build_review_response_table("符合性审查响应对照表", rows)
    return BidDocumentSection(section_title="符合性审查响应对照表", content=content)


def _build_invalid_bid_checklist() -> str:
    """构建无效投标自检表，提供精确定位和核对提示。"""
    items = [
        ("任意一条不满足采购文件★号条款要求", "第三章/技术偏离表", "逐条核对所有★号条款，确保无偏离或正偏离"),
        ("单项产品五条及以上不满足非★号条款要求", "第三章/技术偏离表", "统计负偏离条目数，单包不得≥5条"),
        ("技术参数未与采购文件逐条对应，仅填写\"响应/完全响应\"等笼统表述", "第三章/技术偏离表", "禁止只写\"响应\"，必须逐条填写实际参数值"),
        ("技术参数中未明确品牌、型号、规格、配置", "第三章/技术偏离表", "每包须明确投标品牌、型号，不得留空"),
        ("单项商品报价超过单项预算", "第二章/报价一览表", "每包报价须≤对应包预算"),
        ("未按采购文件要求签字、盖章", "全文", "核对封面、承诺函、报价表等关键页签章"),
        ("响应文件中提供虚假材料", "全文", "确保营业执照、资质证书、授权文件等真实有效"),
        ("授权书无法定代表人/单位负责人签字或未加盖公章", "第一章/授权书", "法人亲笔签字+公章，缺一不可"),
        ("属于围标、串标或依法视为串标", "第一章/围标串标承诺函", "提供承诺函并确保独立投标"),
        ("未按要求参加远程开标或未在规定时间完成解密/签章", "开标环节", "提前测试CA、准时参加开标"),
        ("招标文件规定的其他无效投标情形", "采购文件第X章", "逐条核对采购文件中的特殊要求"),
    ]
    lines = [
        "## 投标无效情形汇总及自检表",
        "| 序号 | 无效情形 | 核对位置 | 自检要点 | 自检结果 |",
        "|---:|---|---|---|---|",
    ]
    for idx, (item, location, check_point) in enumerate(items, 1):
        lines.append(f"| {idx} | {item} | {location} | {check_point} | 【待填写：通过/不通过】 |")
    return "\n".join(lines)

def _build_supplier_commitment_followup(tender: TenderDocument) -> str:
    """构建supplier承诺followup，删除啰嗦的路径说明。"""
    # 删除重复的路径说明提示，依靠资格审查章节本身的引导
    return ""

def _build_public_record_checklist() -> str:
    """构建公共记录checklist。"""
    return "\n".join([
        "### 查询/截图清单",
        "| 平台 | 查询主体 | 建议保留内容 | 建议文件名 |",
        "|---|---|---|---|",
        "| 国家企业信用信息公示系统 | 投标人 | 主体名称、统一社会信用代码、登记状态 | 01_国家企业信用信息公示系统.png |",
        "| 中国执行信息公开网 | 法定代表人/单位负责人 | 查询关键词、查询结果页 | 02_中国执行信息公开网.png |",
        "| 中国裁判文书网 | 投标人/法定代表人 | 查询关键词、结果页 | 03_中国裁判文书网.png |",
        "| 信用中国 | 投标人 | 主体名称、统一社会信用代码、信用结果页 | 04_信用中国.png |",
        "| 中国政府采购网 | 投标人 | 严重违法失信行为记录查询结果 | 05_中国政府采购网.png |",
    ])

def _build_consortium_declaration_block(tender: TenderDocument, today: str) -> str:
    """构建联合体declaration文本块。"""
    allows = _allow_consortium(tender)
    if not allows:
        return f"""## 四、联合体投标声明
本项目采购文件未允许联合体投标，现声明本次以独立投标方式参与，不组成联合体。

投标人名称：{_COMPANY}  
日期：{today}"""
    return f"""## 四、联合体投标声明（单选保留一项）
办理说明：请仅保留与实际投标组织形式一致的一项，其余整段删除，不要只勾选不删除。

### 选项A：独立投标
本次以独立投标方式参与，不组成联合体。

### 选项B：联合体投标
本次以联合体方式参与，并将同步提交联合体协议书及职责分工文件。

投标人名称：{_COMPANY}  
日期：{today}"""

def _build_detail_quote_table(
    tender: TenderDocument,
    tender_raw: str,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    """构建明细报价表格。"""
    lines = [
        "| 序号 | 货物名称 | 规格型号 | 生产厂家 | 品牌 | 单价(元) | 数量 | 总价(元) |",
        "|---:|---|---|---|---|---:|---|---:|",
    ]

    pkgs = packages if packages is not None else tender.packages
    if not pkgs:
        lines.append("| 1 | 【待填写：货物名称】 | 【待填写：规格型号】 | 【待填写：生产厂家】 | 【待填写：品牌】 | 【待填写：单价】 | 【待填写：数量】 | 【待填写：总价】 |")
        lines.append("|  | **合计报价** |  |  |  |  |  | **【待填写：合计报价】** |")
        return "\n".join(lines)

    total_budget = 0.0
    for idx, pkg in enumerate(pkgs, start=1):
        total_budget += pkg.budget
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.append(
            f"| {idx} | {pkg.item_name} | 【待填写：品牌型号】 | 【待填写：生产厂家】 | 【待填写：品牌】 | 【待填写：单价】 | {quantity} | 【待填写：总价】 |"
        )

    lines.append(f"|  | **预算合计（参考）** |  |  |  |  |  | **{_fmt_money(total_budget)}** |")
    lines.append("|  | **投标总报价** |  |  |  |  |  | **【待填写：投标总报价】** |")
    table = "\n".join(lines)
    table += "\n\n> 填写规则：每行“总价(元)” = “单价(元)” × “数量”；底部“投标总报价”应与第三章《报价一览表》保持一致。"
    return table


def _gen_qualification(*args, **kwargs):
    """生成资格审查章节对象。"""
    raise RuntimeError(
        "旧结构生成器 _gen_qualification 已禁用。请改用 build_format_driven_sections()."
    )

def _gen_compliance(*args, **kwargs):
    """生成符合性审查章节对象。"""
    raise RuntimeError(
        "旧结构生成器 _gen_compliance 已禁用。请改用 build_format_driven_sections()."
    )
