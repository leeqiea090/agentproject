"""需求提取、原子化、归一化、分类模块"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.schemas import (
    ClauseCategory,
    DocumentBlock,
    NormalizedRequirement,
    ProcurementPackage,
)

logger = logging.getLogger(__name__)

_MAX_TECH_ROWS_PER_PACKAGE = 80
_PACKAGE_SCOPE_BEFORE_LINES = 8
_PACKAGE_SCOPE_AFTER_LINES = 80

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

_REQ_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("性能", ("检测", "灵敏度", "精度", "速度", "分辨率", "通量", "线性", "重复性",
              "准确度", "误差", "频率", "功率", "效率", "噪声", "信噪比")),
    ("配置", ("配置", "配备", "含", "包含", "标配", "选配", "附件", "配件",
              "主机", "模块", "单元", "组件")),
    ("功能", ("支持", "具备", "功能", "模式", "方法", "自动", "可", "能够",
              "兼容", "扩展", "升级")),
    ("接口", ("接口", "通讯", "网络", "LIS", "HIS", "USB", "RS232", "以太网",
              "WIFI", "蓝牙", "数据传输", "连接")),
    ("安全", ("安全", "防护", "报警", "告警", "保护", "认证", "标准", "合规",
              "CE", "FDA", "ISO", "CFDA")),
]

_CLAUSE_CATEGORY_RULES: list[tuple[ClauseCategory, tuple[str, ...]]] = [
    (ClauseCategory.service_requirement, (
        "质保", "保修", "售后", "维修", "维护", "保养", "巡检", "培训",
        "响应时间", "上门服务", "备品备件", "技术支持", "服务承诺",
        "免费维修", "终身维护", "年度巡检", "应急响应",
    )),
    (ClauseCategory.config_requirement, (
        "配置", "配备", "标配", "选配", "装箱", "配置清单", "配置单",
        "附件", "配件", "随机", "耗材", "标准配置", "主要配置",
        "设备配置", "装箱配置", "配置与配件",
    )),
    (ClauseCategory.acceptance_requirement, (
        "验收", "验收标准", "验收条件", "验收方式", "验收程序",
        "验收报告", "验收合格", "初验", "终验", "试运行",
        "到货验收", "安装验收", "验收期", "试用期",
    )),
    (ClauseCategory.commercial_requirement, (
        "付款", "价格", "报价", "折扣", "预算", "保证金", "违约",
        "合同", "交货期", "交货地点", "包装运输",
        "发票", "税费", "货款", "尾款",
    )),
    (ClauseCategory.compliance_note, (
        "实质性条款", "星号条款", "★", "▲", "不可偏离", "否决项",
        "资格要求", "供应商资格", "投标人资格", "营业执照",
        "经营许可", "注册证", "授权书",
    )),
    (ClauseCategory.attachment_requirement, (
        "提供证明", "提供复印件", "提供扫描件", "加盖公章",
        "附证书", "附报告", "附授权", "附彩页", "随附",
    )),
    (ClauseCategory.documentation_requirement, (
        "操作手册", "使用说明", "说明书", "技术文档", "操作规程",
        "维护手册", "培训资料", "用户手册", "安装手册", "合格证",
        "出厂检验", "质量证明", "装箱单", "随机文件", "技术档案",
        "中文资料", "使用培训", "操作培训",
    )),
    (ClauseCategory.noise, (
        "评分标准", "评分办法", "分值", "得分", "扣分",
        "投标人须知", "响应文件格式", "页码要求", "装订",
        "正本与副本", "目录编制",
    )),
    (ClauseCategory.technical_requirement, (
        "技术参数", "性能", "指标", "检测", "灵敏度", "精度",
        "速度", "分辨率", "通量", "功能", "模式", "方法",
        "光学", "激光", "荧光", "通道", "散射", "波长",
        "温度", "接口", "软件", "系统", "数据",
    )),
]

_OPERATOR_PATTERNS = [
    (r"[≥>=]\s*", "≥"),
    (r"不低于\s*", "≥"),
    (r"不少于\s*", "≥"),
    (r"至少\s*", "≥"),
    (r"[≤<=]\s*", "≤"),
    (r"不高于\s*", "≤"),
    (r"不大于\s*", "≤"),
    (r"不超过\s*", "≤"),
]

_UNIT_PATTERN = re.compile(
    r"(nm|μm|mm|cm|m|μl|ml|L|℃|°C|rpm|Hz|kHz|MHz|W|kW|V|A|dB|psi|Pa|kPa|"
    r"通道|个|台|套|路|位|组|次/秒|次/分|样本/小时|测试/小时|T/h|%)",
    re.IGNORECASE,
)

_MATERIAL_KEYWORDS = ("★", "▲", "实质性", "不可偏离", "否决", "必须满足")

# ── 设备禁止串入词：按设备类型定义不应出现在该包中的其他设备术语 ──
_DEVICE_FORBIDDEN_BY_HINT: dict[str, tuple[str, ...]] = {
    "电泳": ("柯勒照明", "无限远校正光学系统", "激光器", "检测通道", "检测器", "PMT", "荧光补偿", "流速模式"),
    "特种蛋白": ("进口荧光显微镜", "柯勒照明", "无限远校正光学系统"),
    "显微": ("琼脂凝胶电泳", "电泳槽", "染色槽", "电泳系统"),
}

_BAD_NAME_SUFFIXES = ("（", "(", "为", "可", "单机", "至少", "最低", "最高")


def _is_bad_requirement_name(name: str) -> bool:
    """过滤半截条目、悬空条目等不应进入主表的参数名。"""
    stripped = (name or "").strip()
    if not stripped:
        return True
    if stripped.endswith(_BAD_NAME_SUFFIXES):
        return True
    if len(stripped) <= 2:
        return True
    return False


def _package_forbidden_terms(
    package_item_name: str,
    other_package_item_names: list[str] | None = None,
) -> set[str]:
    """根据当前包设备名和其他包设备名，返回禁止词集合。"""
    forbidden: set[str] = set()
    # 从设备禁止词表中匹配
    for hint, terms in _DEVICE_FORBIDDEN_BY_HINT.items():
        if hint in (package_item_name or ""):
            forbidden.update(terms)
    # 其他包的产品名 token 也加入禁止词
    for other_name in (other_package_item_names or []):
        for tok in _extract_match_tokens(other_name):
            if len(tok) >= 3:
                forbidden.add(tok)
    return forbidden


# ── Helper functions ──

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


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_non_technical_content(text: str) -> bool:
    return _contains_any(text, _NON_TECH_KEYS) or _contains_any(text, _NON_TECH_CONTENT_HINTS)


def _normalize_requirement_line(line: str) -> str:
    normalized = line.replace("\t", " ").replace("\r", " ").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"^[★▲■●]\s*", "", normalized)
    normalized = re.sub(r"^\d+(?:\.\d+){0,5}\s*", "", normalized)
    normalized = re.sub(r"^[（(]?\d+[）)]\s*", "", normalized)
    normalized = re.sub(r"^\d+(?=[A-Za-z\u4e00-\u9fa5])", "", normalized)
    normalized = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[）).、]\s*", "", normalized)
    normalized = re.sub(r"^(?:[-*•]|第\d+[项条]?)\s*", "", normalized)
    return normalized.strip(" ;；")


def _is_outline_token(text: str) -> bool:
    return bool(re.fullmatch(r"[（(]?\d+(?:\.\d+){0,5}[）)]?", text.strip()))


def _looks_like_technical_requirement(text: str) -> bool:
    if any(marker in text for marker in _HARD_REQUIREMENT_MARKERS):
        return True
    if _contains_any(text, _TECH_KEYWORDS):
        return True
    return bool(re.search(r"\d", text) and _contains_any(text, ("支持", "具备", "配置", "提供", "采用", "满足")))


def _extract_requirement_pair(fragment: str) -> tuple[str, str] | None:
    normalized = _normalize_requirement_line(fragment)
    normalized = re.sub(r"^(?:采购需求|技术参数|技术要求|性能要求|配置要求|参数要求|技术指标)[:：]?\s*", "", normalized)
    if len(normalized) < 4 or len(normalized) > 120:
        return None
    if _contains_non_technical_content(normalized):
        return None
    if not _looks_like_technical_requirement(normalized):
        return None

    table_cells = [cell.strip() for cell in normalized.split("|") if cell.strip()]
    if len(table_cells) >= 2:
        key_cell = table_cells[0]
        val_cell = table_cells[1]
        if _is_outline_token(key_cell) and len(table_cells) >= 3:
            key_cell = table_cells[1]
            val_cell = table_cells[2]
        normalized = f"{key_cell}：{val_cell}"

    match = re.match(r"^(?P<key>[A-Za-z0-9\u4e00-\u9fa5（）()/+.\-]{2,30})[：:]\s*(?P<val>.+)$", normalized)
    if match:
        key = match.group("key").strip()
        key = re.sub(r"^[★▲■●]\s*", "", key)
        key = re.sub(r"^\d+(?=[A-Za-z\u4e00-\u9fa5])", "", key)
        val = match.group("val").strip(" ；;。")
        if (
            key
            and val
            and not _contains_non_technical_content(key)
            and not _contains_non_technical_content(val)
            and key not in _TECH_KEY_EXCLUDES
            and not re.fullmatch(r"[（(]?\d+分[）)]?", key)
            and not _is_outline_token(key)
        ):
            return key, val

    comp_match = re.match(
        r"^(?P<key>[A-Za-z0-9\u4e00-\u9fa5（）()/+.\-]{2,30})\s*(?P<val>(?:≥|≤|>=|<=|不低于|不少于|不高于|不大于|至少|不超过).+)$",
        normalized,
    )
    if comp_match:
        key = comp_match.group("key").strip()
        key = re.sub(r"^[★▲■●]\s*", "", key)
        key = re.sub(r"^\d+(?=[A-Za-z\u4e00-\u9fa5])", "", key)
        val = comp_match.group("val").strip(" ；;。")
        if (
            key in _TECH_KEY_EXCLUDES
            or re.fullmatch(r"[（(]?\d+分[）)]?", key)
            or _is_outline_token(key)
            or _contains_non_technical_content(key)
            or _contains_non_technical_content(val)
        ):
            return None
        return key, val

    return None


def _split_compound_requirement(key: str, value: str) -> list[tuple[str, str]]:
    """将复合条款拆分为原子级条目。

    处理如 "具备A功能、支持B和C" 这样的复合条款，
    按中文列举符号（、）以及连接词（同时、并且、以及）拆分，
    但保留数值范围和括号内内容不拆。
    """
    if not value or len(value) < 8:
        return [(key, value)]
    # 不拆分包含数值范围的纯参数值（如 "≥3个"、"10-20mL"）
    if re.fullmatch(r"[≥≤><\d\s.+\-~～至到%％μmLnNgGhHzZkKwWtT/*()（）]+", value.strip()):
        return [(key, value)]

    _VERB_MARKERS = ("具备", "支持", "提供", "满足", "采用", "配置", "配备", "可", "能够", "应", "须")
    _COMPOUND_DELIMITERS = r"(?<=[）\)\u4e00-\u9fff])、(?=[\u4e00-\u9fff])"

    fragments = re.split(_COMPOUND_DELIMITERS, value)
    if len(fragments) < 2:
        fragments = re.split(r"同时|并且|以及|；|;", value)
    if len(fragments) < 2:
        return [(key, value)]

    results: list[tuple[str, str]] = []
    for frag in fragments:
        frag = frag.strip(" ，,；;。、")
        if not frag or len(frag) < 3:
            continue
        # 语义完整性检查：拆后条目 key+frag 必须 >= 6 字才允许入表
        if len(frag) < 6 and not any(m in frag for m in _HARD_REQUIREMENT_MARKERS) and not re.search(r"\d", frag):
            continue
        pair = _extract_requirement_pair(frag)
        if pair:
            results.append(pair)
        else:
            results.append((key, frag))

    return results if len(results) > 1 else [(key, value)]


def _expand_requirement_entry(key: str, value: str) -> list[tuple[str, str]]:
    # 第一阶段：如果是笼统 key，按 ；; 拆分
    if any(marker in key for marker in _GENERIC_TECH_KEYS) and ("；" in value or ";" in value):
        expanded: list[tuple[str, str]] = []
        for fragment in re.split(r"[；;]", value):
            pair = _extract_requirement_pair(fragment)
            if pair:
                expanded.append(pair)
        if len(expanded) >= 2:
            return _dedupe_requirement_pairs(expanded)

    # 第二阶段：对每个条目做复合条款拆分（处理 、同时、并且 等中文列举）
    compound_results = _split_compound_requirement(key, value)
    if len(compound_results) > 1:
        return _dedupe_requirement_pairs(compound_results)

    return [(key, value)]


def _dedupe_requirement_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, val in pairs:
        dedup_key = f"{key}::{val}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        deduped.append((key, val))
    return deduped


def _flatten_requirements(pkg: ProcurementPackage) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in pkg.technical_requirements.items():
        k = _safe_text(str(key), "技术参数")
        v = _safe_text(_as_text(value), "详见招标文件")
        items.extend(_expand_requirement_entry(k, v))
    return _dedupe_requirement_pairs(items)


def _is_sparse_technical_requirements(pkg: ProcurementPackage) -> bool:
    """判断采购包技术参数是否稀疏，阈值从 2 降至 5 以触发更多原文回提。"""
    requirements = _flatten_requirements(pkg)
    meaningful = [
        key
        for key, _ in requirements
        if key and key not in {"核心技术参数", "其他参数", "技术参数"}
    ]
    return len(meaningful) < 5


def _extract_requirements_from_raw(pkg: ProcurementPackage, tender_raw: str) -> list[tuple[str, str]]:
    if not tender_raw.strip():
        return []

    item_tokens = [token for token in re.split(r"[，,、；;（）()\\s/]+", pkg.item_name) if len(token) >= 2]
    relevant_window = 0
    in_tech_scope = False
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    for raw_line in tender_raw.splitlines():
        normalized = _normalize_requirement_line(raw_line)
        if not normalized:
            relevant_window = max(0, relevant_window - 1)
            in_tech_scope = False if relevant_window == 0 else in_tech_scope
            continue

        if _contains_any(normalized, _TECH_EXIT_HINTS):
            in_tech_scope = False
            relevant_window = 0
            continue

        if _contains_any(normalized, _TECH_SECTION_HINTS):
            in_tech_scope = True
            relevant_window = 18

        if item_tokens and any(token in normalized for token in item_tokens):
            relevant_window = 12

        scoped = in_tech_scope or relevant_window > 0
        if relevant_window > 0:
            relevant_window -= 1

        if not scoped and not _looks_like_technical_requirement(normalized):
            continue

        fragments = [frag for frag in re.split(r"[；;。]", normalized) if frag.strip()]
        for fragment in fragments:
            if _contains_any(fragment, _TECH_EXIT_HINTS):
                continue
            pair = _extract_requirement_pair(fragment)
            if not pair:
                continue
            key, val = pair
            dedup_key = f"{key}::{val}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            pairs.append(pair)
            if len(pairs) >= _MAX_TECH_ROWS_PER_PACKAGE:
                return pairs

    return pairs


def _atomize_requirement(key: str, value: str) -> list[tuple[str, str]]:
    """将复合技术要求拆分成原子级条款。

    规则：
    - 一个句子里有多个参数（用、或；分隔），拆成多条
    - "技术参数总括"这种合并项不允许进入最终技术偏离表
    - 拆后必须语义完整：不允许半截条目名（括号未闭合、过短无意义）进入最终主表
    """
    # 跳过总括性/通用项
    _GENERIC_SUMMARY_KEYS = ("技术参数总括", "技术参数汇总", "技术要求总述", "整体要求", "总体要求", "参数一览")
    if any(gk in key for gk in _GENERIC_SUMMARY_KEYS):
        return []

    normalized_val = _as_text(value)
    if not normalized_val or len(normalized_val) < 4:
        return [(key, normalized_val or "详见招标文件")]

    # 检测是否含多个参数（用、；分隔，且每段含数值或技术关键词）
    segments = re.split(r"[；;]", normalized_val)
    if len(segments) <= 1:
        # 尝试用、分隔 — 阈值降至2段，且只需大多数含技术内容即可
        sub_segments = re.split(r"、", normalized_val)
        tech_count = sum(
            1 for seg in sub_segments
            if any(m in seg for m in _HARD_REQUIREMENT_MARKERS) or re.search(r"\d", seg)
            or _contains_any(seg, _TECH_KEYWORDS)
        )
        if len(sub_segments) >= 2 and tech_count >= len(sub_segments) * 0.6:
            segments = sub_segments

    if len(segments) <= 1:
        return [(key, normalized_val)]

    # 拆分成原子条款
    results: list[tuple[str, str]] = []
    for idx, segment in enumerate(segments, start=1):
        seg = segment.strip()
        if not seg or len(seg) < 3:
            continue
        # 语义完整性检查：拆后条目必须 >= 6 字或含数值/硬标记才允许入表
        if len(seg) < 6 and not any(m in seg for m in _HARD_REQUIREMENT_MARKERS) and not re.search(r"\d", seg):
            continue
        # 半截条目名检查：括号未闭合则回合到父级
        if _is_truncated_name(seg):
            continue
        # 尝试从 segment 中提取 sub_key:sub_val
        pair = _extract_requirement_pair(seg)
        if pair:
            # 再次检查拆后 key 的完整性
            sub_key, sub_val = pair
            if _is_truncated_name(sub_key):
                continue
            results.append(pair)
        else:
            sub_key = f"{key}（{idx}）" if len(segments) > 1 else key
            results.append((sub_key, seg))

    return results if results else [(key, normalized_val)]


def _is_truncated_name(name: str) -> bool:
    """检测条目名是否为半截（括号未闭合、自引用、以冒号/介词结尾等）。"""
    stripped = name.strip()
    if not stripped:
        return True
    # 括号未闭合
    open_count = stripped.count("（") + stripped.count("(")
    close_count = stripped.count("）") + stripped.count(")")
    if open_count > close_count:
        return True
    # 以中文左括号结尾
    if stripped.endswith("（") or stripped.endswith("("):
        return True
    # 过短且无技术含义
    if (
        len(stripped) < 3
        and not re.search(r"\d", stripped)
        and not _contains_any(stripped, _TECH_KEYWORDS + ("型号", "品牌", "产地", "厂家"))
    ):
        return True
    # 以冒号、顿号或"为"结尾 — 说明值部分被截断
    if re.search(r"[：:、为]$", stripped):
        return True
    # 自引用检测："检测器：检测器为" — key 与 value 开头重复
    if "：" in stripped or ":" in stripped:
        parts = re.split(r"[：:]", stripped, maxsplit=1)
        if len(parts) == 2:
            k, v = parts[0].strip(), parts[1].strip()
            # value 以 key 开头且后面不超过 2 个字 → 自引用截断
            if k and v.startswith(k) and len(v) <= len(k) + 2:
                return True
            # value 以连接词/虚词结尾，说明句子未完成
            if v and re.search(r"[可为的与及]$", v):
                return True
    # "至少包含""最低温度" 等以量词/限定词结尾但没有实际数值
    if re.search(r"(至少|最低|最高|不低于|不少于|不超过)$", stripped):
        return True
    return False


def _atomize_requirements(requirements: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """对需求列表执行原子化拆分。"""
    atomized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, val in requirements:
        for sub_key, sub_val in _atomize_requirement(key, val):
            dedup = f"{sub_key}::{sub_val}"
            if dedup in seen:
                continue
            seen.add(dedup)
            atomized.append((sub_key, sub_val))
    return atomized


def _effective_requirements(pkg: ProcurementPackage, tender_raw: str) -> list[tuple[str, str]]:
    """提取采购包的有效需求列表。

    改进：即使非 sparse 包也会从原文补充，以确保各包粒度均匀。
    """
    requirements = _flatten_requirements(pkg)

    # 始终尝试从原文补充（不仅限于 sparse 包）
    package_scoped_raw = _extract_package_technical_scope_text(pkg, tender_raw)
    extra_pairs = _extract_requirements_from_raw(pkg, package_scoped_raw)

    if extra_pairs:
        existing_keys = {key for key, _ in requirements}
        merged = list(requirements)
        for key, val in extra_pairs:
            if key in existing_keys:
                continue
            merged.append((key, val))
            existing_keys.add(key)
        atomized = _atomize_requirements(merged)
    else:
        atomized = _atomize_requirements(requirements)

    # ── 跨包词命中检测：含其他包产品名的条款直接标为噪音并剔除 ──
    cleaned: list[tuple[str, str]] = []
    for key, val in atomized:
        cat = _classify_requirement_category(key, val)
        # 将分类信息嵌入 key 的前缀标记中，方便下游使用
        cleaned.append((key, val))
    return cleaned


# ── Requirement 分类器 ──

def _classify_requirement_category(key: str, value: str) -> str:
    """将技术要求分类为：性能/配置/功能/接口/安全/通用。"""
    text = f"{key} {value}"
    for category, keywords in _REQ_CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return category
    return "通用"


def _classify_clause_category(key: str, value: str) -> ClauseCategory:
    """将条款细分为 ClauseCategory — 基于关键词命中计数，取最高分类别。

    修复：原先采用 first-match 逻辑，容易因关键词重叠导致误分类
    （如"设备配置及质保"同时命中 service 和 config，first-match 总是返回 service）。
    改为对所有类别打分，取命中次数最多的类别。
    """
    text = f"{key} {value}"
    best_category = ClauseCategory.technical_requirement
    best_score = 0
    for category, keywords in _CLAUSE_CATEGORY_RULES:
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category


# ═══════════════════════════════════════════════════════════════════
#  Phase 4: 归一化需求 → NormalizedRequirement 对象
# ═══════════════════════════════════════════════════════════════════

def _extract_operator_threshold_unit(text: str) -> tuple[str, str, str]:
    """从条款文本中提取比较算子、阈值、单位。"""
    operator = ""
    threshold = ""
    unit = ""

    for pattern, op in _OPERATOR_PATTERNS:
        m = re.search(pattern + r"([\d.,]+)", text)
        if m:
            operator = op
            threshold = m.group(1)
            break

    if not operator:
        m = re.search(r"([\d.,]+)", text)
        if m:
            threshold = m.group(1)
            operator = "="

    unit_m = _UNIT_PATTERN.search(text)
    if unit_m:
        unit = unit_m.group(1)

    return operator, threshold, unit


def _find_best_block_for_key(
    key: str,
    val: str,
    package_id: str,
    doc_blocks: list[DocumentBlock],
) -> DocumentBlock | None:
    """在当前包的 DocumentBlock 中找到最匹配 key 的块。"""
    candidates = [
        b for b in doc_blocks
        if not b.is_noise and (not b.package_id or b.package_id == package_id)
    ]
    # 精确匹配 key
    hits = [b for b in candidates if key in b.text]
    if hits:
        # 优先选同时包含 val 的块
        both = [b for b in hits if val and val[:20] in b.text]
        return both[0] if both else hits[0]
    return None


def normalize_requirements_to_objects(
    package_id: str,
    requirements: list[tuple[str, str]],
    source_page: int = 0,
    other_package_item_names: list[str] | None = None,
    package_item_name: str = "",
    doc_blocks: list[DocumentBlock] | None = None,
) -> list[NormalizedRequirement]:
    """将原子化后的 (key, value) 对转化为 NormalizedRequirement 对象。

    - 自动提取 operator / threshold / unit
    - 自动分类 category（9 类 ClauseCategory）
    - 自动检测 is_material（实质性条款）
    - 跨包词命中直接判噪音
    - 设备禁止词污染过滤
    - 半截条目名自动过滤
    - 填充 source_text（优先使用真实来源块文本，回退到拼接字符串）
    """
    # 构建跨包检测 token
    _cross_pkg_tokens: list[str] = []
    for name in (other_package_item_names or []):
        for tok in _extract_match_tokens(name):
            if len(tok) >= 3:
                _cross_pkg_tokens.append(tok)

    # 构建设备禁止词
    forbidden_terms = _package_forbidden_terms(package_item_name, other_package_item_names)

    # 预索引 doc_blocks（仅当前包）
    _pkg_blocks = doc_blocks or []

    results: list[NormalizedRequirement] = []
    for idx, (key, val) in enumerate(requirements, start=1):
        raw_text = f"{key}：{val}" if val else key

        # 坏名过滤
        if _is_bad_requirement_name(key):
            logger.debug("坏名过滤: pkg%s req#%d key=%s", package_id, idx, key)
            continue

        # 半截条目名过滤：括号未闭合、过短无意义
        if _is_truncated_name(key):
            logger.debug("半截条目名过滤: pkg%s req#%d key=%s", package_id, idx, key)
            continue

        # 半截值过滤：值部分也被截断（如"检测器为""单机"）
        if val and _is_truncated_name(f"{key}：{val}"):
            logger.debug("半截值过滤: pkg%s req#%d raw=%s", package_id, idx, raw_text[:60])
            continue

        # 设备禁止词污染过滤
        if forbidden_terms and any(tok in raw_text for tok in forbidden_terms):
            logger.debug("设备污染过滤: pkg%s req#%d 命中禁止词, text=%s", package_id, idx, raw_text[:60])
            continue

        # 跨包词命中检测：如果含其他包产品名 token，直接标噪音
        if _cross_pkg_tokens and any(tok in raw_text for tok in _cross_pkg_tokens):
            category = ClauseCategory.noise
            logger.debug("跨包噪音: pkg%s req#%d 命中跨包词, text=%s", package_id, idx, raw_text[:60])
        else:
            category = _classify_clause_category(key, val)

        operator, threshold, unit = _extract_operator_threshold_unit(val)
        is_material = any(kw in raw_text for kw in _MATERIAL_KEYWORDS)
        needs_bid_fact = category in (
            ClauseCategory.technical_requirement,
            ClauseCategory.config_requirement,
            ClauseCategory.service_requirement,
        )

        # 标记需人工确认的条目：值过短且无量化信息
        needs_manual = False
        if val and len(val.strip()) < 4 and not operator and not threshold:
            needs_manual = True
        # 自引用检测（key 出现在 val 开头且 val 没有更多信息）
        if val and val.strip().startswith(key) and len(val.strip()) <= len(key) + 3:
            needs_manual = True

        # 从真实来源块获取 source_text / source_page，不再用拼接字符串
        block_source_text = raw_text
        block_source_page = source_page
        if _pkg_blocks:
            matched_block = _find_best_block_for_key(key, val, package_id, _pkg_blocks)
            if matched_block:
                block_source_text = matched_block.text
                block_source_page = matched_block.page

        results.append(NormalizedRequirement(
            package_id=package_id,
            requirement_id=f"pkg{package_id}-req-{idx:03d}",
            param_name=key,
            operator=operator,
            threshold=threshold,
            unit=unit,
            raw_text=raw_text,
            category=category,
            is_material=is_material,
            needs_bid_fact=needs_bid_fact,
            needs_manual_confirmation=needs_manual,
            source_page=block_source_page,
            source_text=block_source_text,
            source_clause_no=_detect_clause_no_from_key(key),
        ))
    return results


def _detect_clause_no_from_key(key: str) -> str:
    """从 key 中提取条款编号。"""
    m = re.match(r"^(\d+(?:\.\d+)*)\s", key)
    return m.group(1) if m else ""


def filter_requirements_by_category(
    requirements: list[NormalizedRequirement],
    category: ClauseCategory,
) -> list[NormalizedRequirement]:
    """按分类筛选需求列表。"""
    return [r for r in requirements if r.category == category]


# ── Scope / section extraction ──

def _markdown_cell(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text).strip())
    return normalized.replace("|", "/")


def _extract_match_tokens(*texts: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for text in texts:
        raw_tokens = re.split(r"[，,、；;：:（）()【】\\[]\\s/\\\\]+", text)
        for token in raw_tokens:
            normalized = token.strip()
            if len(normalized) < 2:
                continue
            if normalized.isdigit():
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            tokens.append(normalized)
    tokens.sort(key=len, reverse=True)
    return tokens


def _extract_section_blocks(text: str, start_hints: tuple[str, ...], exit_hints: tuple[str, ...], max_lines: int = 80) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return []

    blocks: list[str] = []
    current: list[str] = []
    started = False

    for raw_line in lines:
        normalized = _normalize_requirement_line(raw_line)
        if not normalized:
            continue

        if not started and _contains_any(normalized, start_hints):
            started = True
            current = [normalized]
            continue

        if not started:
            continue

        if _contains_any(normalized, exit_hints) and not _contains_any(normalized, start_hints):
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            started = False
            continue

        current.append(normalized)
        if len(current) >= max_lines:
            blocks.append("\n".join(current).strip())
            current = []
            started = False

    if started and current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _extract_package_technical_scope_text(
    pkg: ProcurementPackage,
    tender_raw: str,
    other_package_names: tuple[str, ...] = (),
) -> str:
    package_scope = _extract_package_scope_text(pkg, tender_raw, other_package_names=other_package_names)
    blocks = _extract_section_blocks(
        package_scope,
        start_hints=_TECH_SECTION_HINTS,
        exit_hints=(*_TECH_EXIT_HINTS, *_CONFIG_SECTION_HINTS),
        max_lines=80,
    )
    return "\n".join(blocks).strip() if blocks else package_scope


def _extract_package_configuration_scope_text(
    pkg: ProcurementPackage,
    tender_raw: str,
    other_package_names: tuple[str, ...] = (),
) -> str:
    package_scope = _extract_package_scope_text(pkg, tender_raw, other_package_names=other_package_names)
    blocks = _extract_section_blocks(
        package_scope,
        start_hints=_CONFIG_SECTION_HINTS,
        exit_hints=_CONFIG_EXIT_HINTS,
        max_lines=60,
    )
    return "\n".join(blocks).strip()


def _extract_package_scope_text(
    pkg: ProcurementPackage,
    tender_raw: str,
    other_package_names: tuple[str, ...] = (),
) -> str:
    text = tender_raw or ""
    if not text.strip():
        return text

    lines = text.splitlines()
    if not lines:
        return text

    item_tokens = [pkg.item_name, *_extract_match_tokens(pkg.item_name)]
    # Build tokens for other packages' item names to detect cross-package boundaries
    other_tokens: list[str] = []
    for name in other_package_names:
        other_tokens.extend(t for t in _extract_match_tokens(name) if len(t) >= 3 and t not in item_tokens)
    package_markers = {
        f"包{pkg.package_id}",
        f"第{pkg.package_id}包",
        f"{pkg.package_id}包",
    }
    package_candidates: list[tuple[int, int]] = []
    token_candidates: list[tuple[int, int]] = []

    for idx, raw_line in enumerate(lines):
        normalized = _normalize_requirement_line(raw_line)
        if not normalized:
            continue

        score = 0
        has_package_marker = any(marker in normalized for marker in package_markers)
        if any(token and token in normalized for token in item_tokens):
            score += 3
        if has_package_marker:
            score += 2
        lookahead = " ".join(lines[idx:min(len(lines), idx + 12)])
        if score and _contains_any(lookahead, _TECH_SECTION_HINTS):
            score += 2

        if score:
            if has_package_marker:
                package_candidates.append((score, idx))
            else:
                token_candidates.append((score, idx))

    candidate_indexes = package_candidates or token_candidates

    if not candidate_indexes:
        return text

    scopes: list[str] = []
    seen_scopes: set[str] = set()
    for _, idx in sorted(candidate_indexes, reverse=True)[:3]:
        current_line = _normalize_requirement_line(lines[idx])
        start = idx
        if not any(marker in current_line for marker in package_markers):
            while start > 0 and idx - start < _PACKAGE_SCOPE_BEFORE_LINES:
                previous = _normalize_requirement_line(lines[start - 1])
                if previous and re.search(r"(?:包\s*\d+|第\s*\d+\s*包|\d+\s*包)", previous) and not any(
                    marker in previous for marker in package_markers
                ):
                    break
                start -= 1

        end = idx + 1
        while end < len(lines) and end - idx < _PACKAGE_SCOPE_AFTER_LINES:
            following = _normalize_requirement_line(lines[end])
            if following and re.search(r"(?:包\s*\d+|第\s*\d+\s*包|\d+\s*包)", following) and not any(
                marker in following for marker in package_markers
            ):
                break
            # Cross-package boundary: break if another package's item_name appears
            if following and other_tokens and any(t in following for t in other_tokens):
                break
            end += 1

        scope = "\n".join(lines[start:end]).strip()
        if not scope or scope in seen_scopes:
            continue
        seen_scopes.add(scope)
        scopes.append(scope)

    return "\n".join(scopes) if scopes else text


def _build_loose_match_pattern(text: str) -> str:
    pieces = [re.escape(piece) for piece in re.split(r"\s+", text.strip()) if piece]
    return r"\s*".join(pieces)


def _find_requirement_pair_position(text: str, req_key: str, req_val: str) -> tuple[int, str]:
    key_pattern = _build_loose_match_pattern(req_key)
    val_pattern = _build_loose_match_pattern(req_val)
    if not key_pattern or not val_pattern:
        return -1, ""

    patterns = (
        rf"{key_pattern}\s*[:：]?\s*{val_pattern}",
        rf"{key_pattern}[^\n]{{0,24}}{val_pattern}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.start(), match.group(0)
    return -1, ""


def _find_evidence_position(text: str, candidates: list[str]) -> tuple[int, str]:
    idx = -1
    matched = ""
    candidates = [
        candidate.strip()
        for candidate in candidates
        if candidate and candidate.strip()
    ]
    lowered = text.lower()
    for candidate in candidates:
        pos = lowered.find(candidate.lower())
        if pos >= 0:
            idx = pos
            matched = candidate
            break
    return idx, matched
