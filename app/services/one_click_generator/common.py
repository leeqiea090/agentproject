"""一键投标文件生成服务（按固定模板生成，强调格式稳定性）"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from langchain_openai import ChatOpenAI

from app.services.chunking import split_to_blocks
from app.services.requirement_processor import (
    _extract_package_scope_text,
    _normalize_requirement_line,
)

from app.schemas import (
    BidDocumentSection,
    BidEvidenceBinding,
    BidGenerationResult,
    ClauseCategory,
    DocumentMode,
    DraftLevel,
    NormalizedRequirement,
    ProductProfile,
    ProcurementPackage,
    RegressionMetrics,
    TenderDocument,
    TenderSourceBinding,
    ValidationGate,
)

logger = logging.getLogger(__name__)


class BidSectionsValidationError(RuntimeError):
    """最终成稿未通过硬校验时抛出，阻断任何对外输出。"""

    def __init__(
        self,
        gate: ValidationGate,
        reasons: list[str],
        *,
        heal_passes: int = 0,
        message: str | None = None,
    ) -> None:
        self.validation_gate = gate
        self.reasons = reasons
        self.heal_passes = heal_passes
        reason_text = "；".join(reasons) if reasons else "未知原因"
        super().__init__(
            message or f"硬校验未通过，未输出任何内容：{reason_text}"
        )

_MAX_TECH_ROWS_PER_PACKAGE = 80
_PACKAGE_SCOPE_BEFORE_LINES = 8
_PACKAGE_SCOPE_AFTER_LINES = 80

# ── 详细度目标（Detail Targets）──
_DETAIL_TARGETS = {
    "technical_atomic_clauses_per_package": 15,
    "deviation_table_min_rows": 10,
    "narrative_sections_min_chars": 200,
    "evidence_per_item": 1,
    "config_items_min": 5,
    "config_description_min_sentences": 1,
}

# ── 富展开模式 ──
_RICH_EXPANSION_MODE = True

_COMPANY = "[投标方公司名称]"
_LEGAL_REP = "[法定代表人]"
_AUTHORIZED_REP = "[授权代表]"
_PHONE = "[联系电话]"
_ADDRESS = "[联系地址]"

_TEMPLATE_POLLUTION_PREFIXES = (
    "你是",
    "请生成",
    "输出json",
    "输出JSON",
    "markdown格式",
    "Markdown格式",
    "根据以上",
    "以下是",
    "as an ai",
)
_TEMPLATE_POLLUTION_TOKENS = ("{{", "}}", "<!--", "-->")
_TEMPLATE_POLLUTION_INFIX_KEYWORDS = (
    "system:",
    "assistant:",
    "user:",
    "只允许输出",
    "请严格按照",
    "请按以下",
    "根据上述",
    "输出格式",
    "返回json",
    "判定结果：",
    "原文长度",
    "用于内容校验",
    "debug:",
    "trace:",
)
_HARD_REQUIREMENT_MARKERS = ("≥", "≤", ">=", "<=", "不低于", "不少于", "不高于", "不大于", "至少")
_TECH_SECTION_HINTS = ("技术参数", "技术要求", "采购需求", "性能要求", "配置要求", "参数要求", "技术指标")
_GENERIC_TECH_KEYS = ("技术参数", "主要技术参数", "性能要求", "技术指标", "参数要求", "核心技术参数")
_CONFIG_REQUIREMENT_KEYS = (
    "设备配置",
    "配置与配件",
    "设备配置与配件",
    "配置清单",
    "主要配置",
    "标准配置",
    "装箱配置",
    "装箱配置单",
)
_CONFIG_SECTION_HINTS = _CONFIG_REQUIREMENT_KEYS
_TECH_KEYWORDS = (
    "激光", "荧光", "通道", "散射", "检测", "样本", "进样", "上样", "分析", "分辨率",
    "灵敏度", "软件", "模块", "配置", "波长", "流速", "补偿", "绝对计数", "体积",
    "温度", "接口", "兼容", "系统", "主机", "试剂", "耗材", "数据", "报表",
)
_NON_TECH_KEYS = (
    "项目名称", "项目编号", "采购人", "采购单位", "代理机构", "采购方式", "预算", "最高限价",
    "联系人", "联系电话", "地址", "日期", "时间", "供应商", "投标人", "评分", "资格",
    "商务", "售后", "付款", "质保", "交货地点", "交货时间", "开标", "响应文件",
)
_NON_TECH_CONTENT_HINTS = (
    "投标文件格式特殊要求", "投标文件格式", "响应文件格式", "正本与副本", "A4纸", "装订成册",
    "签字确认", "科室负责人", "目录编制", "页码要求",
)
_TECH_EXIT_HINTS = (
    "评分标准", "评分办法", "评分", "分值", "得分", "扣分", "商务部分", "商务条款",
    "合同条款", "付款方式", "包装运输方案", "配货单", "设备配置及参数清单", "耗材名称",
    "投标人须知", "乙方承诺", "甲方", "乙方", "争议的解决", "违约责任", "中国政府采购网",
    "技术部分 /", "技术部分", "投标文件格式特殊要求", "投标文件格式", "响应文件格式",
    "正本与副本", "装订成册", "装箱配置", "装箱配置单", "配置清单", "标准配置",
    "质保", "售后", "质疑", "投诉",
)
_TECH_KEY_EXCLUDES = {
    "乙方承诺",
    "甲方承诺",
    "序号",
    "耗材名称",
    "设备配置及参数清单",
    "包装运输方案",
}
_REGION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("北京市", ("北京",)),
    ("天津市", ("天津",)),
    ("上海市", ("上海",)),
    ("重庆市", ("重庆",)),
    ("河北省", ("河北", "石家庄")),
    ("山西省", ("山西", "太原")),
    ("辽宁省", ("辽宁", "沈阳")),
    ("吉林省", ("吉林", "长春")),
    ("黑龙江省", ("黑龙江", "哈尔滨")),
    ("江苏省", ("江苏", "南京")),
    ("浙江省", ("浙江", "杭州")),
    ("安徽省", ("安徽", "合肥")),
    ("福建省", ("福建", "福州", "厦门")),
    ("江西省", ("江西", "南昌")),
    ("山东省", ("山东", "济南", "青岛")),
    ("河南省", ("河南", "郑州")),
    ("湖北省", ("湖北", "武汉")),
    ("湖南省", ("湖南", "长沙")),
    ("广东省", ("广东", "广州", "深圳")),
    ("海南省", ("海南", "海口")),
    ("四川省", ("四川", "成都")),
    ("贵州省", ("贵州", "贵阳")),
    ("云南省", ("云南", "昆明")),
    ("陕西省", ("陕西", "西安")),
    ("甘肃省", ("甘肃", "兰州")),
    ("青海省", ("青海", "西宁")),
    ("内蒙古自治区", ("内蒙古", "呼和浩特")),
    ("广西壮族自治区", ("广西", "南宁")),
    ("西藏自治区", ("西藏", "拉萨")),
    ("宁夏回族自治区", ("宁夏", "银川")),
    ("新疆维吾尔自治区", ("新疆", "乌鲁木齐")),
)
_CONFIG_EXIT_HINTS = (
    "质保",
    "售后",
    "付款方式",
    "商务条款",
    "合同条款",
    "评分标准",
    "评分办法",
    "技术参数",
    "技术要求",
    "质疑",
    "投诉",
    "投标人须知",
)
_CONFIG_ITEM_UNITS = ("台套", "台", "套", "个", "把", "本", "件", "组", "副", "支", "块", "张", "盒", "瓶", "根", "条", "份", "只")
_PENDING_BIDDER_RESPONSE = "待核实（需填入投标产品实参）"


def _today() -> str:
    return datetime.now().strftime("%Y年%m月%d日")


def _safe_text(text: str | None, default: str = "详见招标文件") -> str:
    if text is None:
        return default
    stripped = str(text).strip()
    return stripped or default


def _normalize_commitment_term(text: str | None, default: str = "按招标文件及合同约定执行") -> str:
    normalized = _safe_text(text, default)
    if not normalized:
        return default
    if any(token in normalized for token in ("[待填写]", "____", "＿", "乙方", "甲方在支付", "本合同项下")):
        return default
    if re.search(r"为\s{2,}[年月日天个]", normalized):
        return default
    if len(normalized) > 48 and any(token in normalized for token in ("合同", "发票", "违约")):
        return default
    return normalized


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


def _tender_context_text(tender: TenderDocument) -> str:
    eval_items = " ".join(f"{k}:{v}" for k, v in tender.evaluation_criteria.items())
    package_names = " ".join(pkg.item_name for pkg in tender.packages)
    package_requirements = " ".join(
        " ".join(str(v) for v in pkg.technical_requirements.values())
        for pkg in tender.packages
    )
    return " ".join(
        [
            tender.project_name,
            tender.project_number,
            tender.purchaser,
            tender.agency,
            tender.procurement_type,
            tender.special_requirements,
            eval_items,
            package_names,
            package_requirements,
        ]
    )


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_non_technical_content(text: str) -> bool:
    return _contains_any(text, _NON_TECH_KEYS) or _contains_any(text, _NON_TECH_CONTENT_HINTS)


def _is_medical_project(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    return _contains_any(
        context,
        ("医疗", "器械", "检验", "试剂", "诊断", "流式", "医院"),
    )


def _requires_sme_declaration(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    return _contains_any(
        context,
        ("中小企业", "小微", "监狱企业", "残疾人福利性单位", "价格扣除", "声明函"),
    )


def _allow_consortium(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    if "不接受联合体" in context:
        return False
    return "联合体" in context and _contains_any(context, ("允许联合体", "接受联合体", "可联合体"))


def _has_imported_clues(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    return _contains_any(context, ("进口", "原装", "境外"))


def _detect_procurement_region(tender: TenderDocument) -> str:
    preferred_contexts = [
        _safe_text(tender.purchaser, ""),
        _safe_text(tender.project_name, ""),
    ]
    fallback_contexts = [
        _safe_text(tender.special_requirements, ""),
        _safe_text(tender.agency, ""),
    ]

    for context in [*preferred_contexts, *fallback_contexts]:
        for region, keywords in _REGION_KEYWORDS:
            if _contains_any(context, keywords):
                return region
    return ""


def _supplier_commitment_title(tender: TenderDocument) -> str:
    region = _detect_procurement_region(tender)
    return f"{region}政府采购供应商资格承诺函" if region else "政府采购供应商资格承诺函"


def _package_scope(
    tender: TenderDocument,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    pkgs = packages if packages is not None else tender.packages
    if not pkgs:
        return "全部包"
    return "、".join(f"包{pkg.package_id}" for pkg in pkgs)

def _infer_package_quantity(pkg: ProcurementPackage, tender_raw: str) -> int:
    """
    数量一律以 TenderParser 已经写入的 pkg.quantity 为准。
    one_click_generator 阶段不再二次从原文猜数量，避免被技术参数里的
    “3针 / 2次 / 12色 / 40管”等数字误伤，导致不同版本生成结果抖动。
    """
    try:
        qty = int(pkg.quantity)
    except (TypeError, ValueError):
        qty = 0

    if qty > 0:
        return qty

    return 1

def _package_detail_lines(
    tender: TenderDocument,
    tender_raw: str,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    pkgs = packages if packages is not None else tender.packages
    if not pkgs:
        return "- 包信息：详见招标文件。"

    lines: list[str] = []
    for pkg in pkgs:
        delivery = _safe_text(pkg.delivery_time, "按招标文件约定")
        place = _safe_text(pkg.delivery_place, "采购人指定地点")
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.append(
            f"- 包{pkg.package_id}：{pkg.item_name}；数量：{quantity}；预算：{_fmt_money(pkg.budget)}元；"
            f"交货期：{delivery}；交货地点：{place}"
        )
    return "\n".join(lines)




def _quote_overview_table(
    tender: TenderDocument,
    tender_raw: str,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    headers = [
        "| 序号(包号) | 货物名称 | 数量 | 预算金额(元) | 投标报价(元) | 交货期 |",
        "|---|---|---:|---:|---:|---|",
    ]
    pkgs = packages if packages is not None else tender.packages
    rows: list[str] = []

    if pkgs:
        total_budget = 0.0
        for idx, pkg in enumerate(pkgs, start=1):
            total_budget += pkg.budget
            quantity = _infer_package_quantity(pkg, tender_raw)
            rows.append(
                f"| {idx}（{pkg.package_id}） | {pkg.item_name} | {quantity} | "
                f"{_fmt_money(pkg.budget)} | 【待填写：包{pkg.package_id}投标报价】 | {_safe_text(pkg.delivery_time, '按招标文件约定')} |"
            )
        rows.append(
            f"|  | **预算合计（参考）** |  | **{_fmt_money(total_budget)}** |  |  |"
        )
        rows.append(
            "|  | **投标总报价** |  |  | **【待填写：投标总报价】** |  |"
        )
    else:
        rows.append("| 1 | 【待填写：货物名称】 | 【待填写：数量】 | 【待填写：预算金额】 | 【待填写：投标报价】 | 【待填写：交货期】 |")

    table = "\n".join(headers + rows)
    table += "\n\n> 填写规则：每包“投标报价(元)”应与第四章《报价明细表》中对应包总价一致；“投标总报价”应等于各包投标报价合计。"
    return table

# ─── 分类过滤与包隔离辅助函数 ───

def filter_rows_by_category(
    rows: list[dict[str, Any]],
    allowed_categories: set[str],
) -> list[dict[str, Any]]:
    """过滤行列表，只保留指定分类的行。空 category 视为通过。"""
    return [
        r for r in rows
        if not r.get("category") or r.get("category") in allowed_categories
    ]


def validate_package_isolation(
    rows: list[dict[str, Any]],
    expected_package_id: str,
) -> list[dict[str, Any]]:
    """过滤行列表，只保留指定 package_id 的行。"""
    return [
        r for r in rows
        if not r.get("package_id") or r.get("package_id") == expected_package_id
    ]
