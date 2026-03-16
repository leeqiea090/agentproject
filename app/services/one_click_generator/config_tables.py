from __future__ import annotations

import logging
import re
from typing import Any

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.response_tables as _response_tables
import app.services.evidence_binder as _evidence_binder
import app.services.requirement_processor as _requirement_processor
from app.schemas import ClauseCategory, ProcurementPackage
from app.services.one_click_generator.common import (
    _CONFIG_EXIT_HINTS,
    _CONFIG_ITEM_UNITS,
    _CONFIG_REQUIREMENT_KEYS,
    _CONFIG_SECTION_HINTS,
    _as_text,
    _contains_any,
    _safe_text,
)
from app.services.one_click_generator.response_tables import (
    _build_requirement_rows,
    _fuzzy_spec_lookup,
    _row_is_usable_for_package,
    _structured_requirements_for_package,
)
from app.services.requirement_processor import (
    _classify_clause_category,
    _effective_requirements,
    _extract_package_configuration_scope_text,
    _markdown_cell,
    _normalize_requirement_line,
    _package_forbidden_terms,
)

logger = logging.getLogger(__name__)


def _normalize_main_param_note(raw_value: Any, has_real_response: bool) -> str:
    """归一化主参数备注文本。

    只有当招标文件中明确读取到真实偏离结论时才保留原值；
    空值、占位符一律返回待确认——禁止自动写"无偏离"。
    """
    text = _safe_text(raw_value, "")
    bad_values = {"", "—", "-", "待填写", "【待填写】", "[待填写]", "待核实", "待补充", "无偏离"}
    if text in bad_values or "待填写" in text:
        return "待确认"
    return text

def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _response_tables, _evidence_binder, _requirement_processor,):
    __reexport_all(_module)

del _module
def _classify_config_item(name: str) -> str:
    """
    配置项分类要优先保证“可人工审核”：
    - 先判软件，再判随机文件/培训资料，再判耗材，再判附件，最后才判核心模块
    - 严禁用单字“液”判断耗材，避免“液晶显示器”误伤
    """
    n = name.strip()

    if any(k in n for k in ("软件", "程序", "平台", "分析软件", "应用软件", "工作站软件")):
        return "配套软件"

    if any(k in n for k in ("说明书", "文件", "手册", "合格证", "报告", "彩页", "装箱单", "保修卡")):
        return "随机文件"

    if any(k in n for k in ("安装", "培训", "调试", "指导", "服务")):
        return "安装/培训资料"

    if any(k in n for k in ("试剂", "耗材", "微球", "清洗液", "鞘液", "废液桶", "流式管", "滤芯", "墨盒", "色带")):
        return "初始耗材"

    if any(k in n for k in ("显示器", "打印机", "稳压电源", "UPS", "附件", "配件", "接头", "适配", "支架", "台车", "推车", "底座", "托盘", "电源线", "数据线", "连接线", "电缆", "网线")):
        return "标准附件"

    if any(k in n for k in ("主机", "整机", "检测主机", "分析主机", "检测单元", "核心模块")):
        return "核心模块"

    return "标准附件"

def _looks_like_service_config_noise(name: str, remark: str = "") -> bool:
    """过滤服务项/承诺项，这些不应该出现在配置清单中"""
    text = f"{_as_text(name)} {_as_text(remark)}"
    # 扩展服务噪音识别词汇
    service_tokens = (
        "售后", "服务要求", "免费更新", "响应速度", "维护保养",
        "培训", "安装调试", "验收", "送货", "卸货", "技术服务人员",
        "质保期", "保修", "上门服务", "巡检", "保养频次",
        "响应时限", "到场时间", "故障响应", "技术支持",
        "更新服务", "升级服务", "系统升级", "软件升级",
        "远程支持", "电话支持", "在线支持", "培训计划",
        "操作培训", "维修培训", "使用培训", "现场培训",
        "备件储备", "配件供应", "耗材供应", "试剂供应",
        "承担费用", "免费", "不收费", "包含", "含在",
        "双向LIS", "LIS接口费用", "接口费用", "对接费用",
        "运输费用", "包装费用", "安装费", "调试费",
    )
    # 纯服务描述模式
    pure_service_patterns = (
        r"^.{0,3}(提供|承诺|负责|保证|确保).{1,15}(服务|支持|维护|保养|培训|验收)",
        r"^.{0,3}(免费|不收费).{1,20}",
        r"^.{0,3}(售后|质保|保修|维修).{1,15}(要求|标准|方案|计划)",
    )

    if any(token in text for token in service_tokens):
        return True

    import re
    if any(re.search(pattern, text) for pattern in pure_service_patterns):
        return True

    return False

def _config_dedup_tokens(name: str) -> set[str]:
    """提取配置项名称的 token 集合用于 Jaccard 去重。"""
    return {t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", name.strip()) if len(t) >= 2}


def _normalize_config_item_name(name: str) -> str:
    """归一化配置项名称。"""
    normalized = _safe_text(name, "")
    normalized = re.sub(r"^[★▲■●\s]+", "", normalized)
    normalized = re.sub(r"^(?:配置|配件)\s*[:：]?\s*", "", normalized)
    normalized = re.sub(r"^配置\s*\d+\s*", "", normalized)
    normalized = re.sub(r"^[（(]?\d+(?:\.\d+)?[）)]?\s*", "", normalized)
    return normalized.strip(" ：:;；,，。")


def _is_noise_config_item(name: str, remark: str = "") -> bool:
    """判断配置项是否属于噪声项。"""
    normalized = _normalize_config_item_name(name)
    if not normalized:
        return True
    if normalized in {
        "设备总台数",
        "总台数",
        "如有多个配置请另行加行",
        "序号",
        "要求",
        "备注说明",
        "装箱配置单",
        "装箱配置",
        "配置清单",
        "标准配置",
        "设备配置",
        "设备配置与配件",
        "配置与配件",
        "主要配置",
    }:
        return True
    if re.fullmatch(r"配置\s*\d+", normalized):
        return True
    if "第" in normalized and "页" in normalized:
        return True
    if "另行加行" in normalized:
        return True
    if "待补充" in normalized or "待填写" in normalized:
        return True
    if remark and "总台数" in remark:
        return True
    return False


def _looks_like_config_requirement_key(key: str) -> bool:
    """判断 technical_requirements 中的键是否表示配置清单/装箱配置。"""
    normalized = _normalize_config_item_name(key)
    if not normalized:
        return False
    if re.fullmatch(r"(?:设备)?配置\s*\d+", normalized):
        return True
    return any(
        marker in normalized
        for marker in (
            "设备配置",
            "设备配置与配件",
            "配置与配件",
            "装箱配置单",
            "装箱配置",
            "配置清单",
            "主要配置",
            "主要配置功能",
            "标准配置",
            "随机附件",
            "零配件清单",
        )
    )


def _default_config_unit(name: str) -> str:
    """根据配置项名称推断默认单位。"""
    normalized = _normalize_config_item_name(name)
    if any(token in normalized for token in ("主机", "整机", "冷水机", "工作站", "显示器", "打印机", "UPS", "电源")):
        return "台"
    if any(token in normalized for token in ("软件",)):
        return "套"
    if any(token in normalized for token in ("系统", "模块", "组件")):
        return "套"
    if any(token in normalized for token in ("说明书", "文件", "手册", "装箱单", "合格证", "保修卡", "报告", "彩页")):
        return "份"
    if "扫码器" in normalized:
        return "把"
    if any(token in normalized for token in ("试剂", "微球")):
        return "盒"
    if any(token in normalized for token in ("头钉", "探头", "按钮", "报警仪", "底座", "头托", "连接器", "辐照杯", "板", "泵")):
        return "个"
    return "项"


def _guess_config_qty_unit(name: str, raw_value: Any) -> tuple[str, str]:
    """从配置值中推断数量与单位。"""
    text = _safe_text(raw_value, "")
    match = re.search(
        r"(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>台|套|个|把|本|件|组|副|支|块|张|份|盒|台套)?",
        text,
    )
    qty = "1"
    unit = _default_config_unit(name)
    if match and match.group("qty"):
        qty = match.group("qty")
        unit = match.group("unit") or unit
    elif isinstance(raw_value, (int, float)):
        qty = str(int(raw_value)) if float(raw_value).is_integer() else str(raw_value)
    elif text:
        qty = "1"
    return qty, unit


def _append_config_item(
    parsed_items: list[tuple[str, str, str, str]],
    seen_names: set[str],
    *,
    name: str,
    unit: str,
    qty: str,
    remark: str,
) -> None:
    """统一追加配置项并做基础过滤。"""
    normalized_name = _normalize_config_item_name(name)
    if not normalized_name or normalized_name in seen_names:
        if normalized_name and normalized_name in seen_names:
            for idx, (existing_name, existing_unit, existing_qty, existing_remark) in enumerate(parsed_items):
                if existing_name != normalized_name:
                    continue
                should_upgrade = (
                    (existing_unit == "项" and unit and unit != "项")
                    or (existing_qty in {"", "1"} and qty not in {"", "1"})
                )
                if should_upgrade:
                    parsed_items[idx] = (
                        normalized_name,
                        unit or existing_unit,
                        qty or existing_qty,
                        remark or existing_remark,
                    )
                break
        return
    if not qty or not qty.replace(".", "", 1).isdigit():
        qty = "1"
    if not unit:
        unit = _default_config_unit(normalized_name)
    if _is_noise_config_item(normalized_name, remark):
        return
    seen_names.add(normalized_name)
    parsed_items.append((normalized_name, unit, qty, remark))


def _extract_config_items_from_package_requirements(
    pkg: ProcurementPackage,
    parsed_items: list[tuple[str, str, str, str]],
    seen_names: set[str],
) -> None:
    """优先消费解析器已经提取到的 package-level 配置字典。"""
    for key, raw_value in (getattr(pkg, "technical_requirements", None) or {}).items():
        if not _looks_like_config_requirement_key(_safe_text(key, "")):
            continue

        if isinstance(raw_value, dict):
            for item_name, item_value in raw_value.items():
                normalized_name = _normalize_config_item_name(item_name)
                qty, unit = _guess_config_qty_unit(normalized_name, item_value)
                remark = _classify_config_item(normalized_name)
                _append_config_item(
                    parsed_items,
                    seen_names,
                    name=normalized_name,
                    unit=unit,
                    qty=qty,
                    remark=remark,
                )
            continue

        value_text = _as_text(raw_value)
        if not value_text:
            continue
        for fragment in [frag.strip() for frag in re.split(r"[，,；;、]", value_text) if frag.strip()]:
            normalized = _normalize_requirement_line(fragment)
            if not normalized:
                continue
            match = re.match(
                r"^(?P<name>.+?)(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>台|套|个|把|本|件|组|副|支|块|张|份|盒|台套)?$",
                normalized,
            )
            if match:
                name = _normalize_config_item_name(match.group("name"))
                qty = match.group("qty")
                unit = match.group("unit") or _default_config_unit(name)
            else:
                name = _normalize_config_item_name(normalized)
                qty = "1"
                unit = _default_config_unit(name)
            remark = _classify_config_item(name)
            _append_config_item(
                parsed_items,
                seen_names,
                name=name,
                unit=unit,
                qty=qty,
                remark=remark,
            )


_DERIVED_CONFIG_CANDIDATE_PATTERN = re.compile(
    r"([A-Za-z0-9\u4e00-\u9fa5（）()\-+]{2,24}?"
    r"(?:主机|整机|检测主机|分析主机|检测单元|"
    r"工作站|数据工作站|分析工作站|"
    r"软件系统|分析软件|应用软件|管理软件|控制软件|软件|"
    r"系统|模块|单元|组件|部件|"
    r"探头|扫码器|报警仪|传感器|检测器|"
    r"冷水机|稳压电源|UPS|不间断电源|电源|"
    r"显示器|打印机|触摸屏|液晶屏|"
    r"按钮|门锁系统|底座手柄|底座|支架|台车|推车|"
    r"头托|头钉|连接器|接口|适配器|"
    r"球管|辐照杯|离子泵|面板|控制面板|"
    r"流动室|试剂架|比色杯|样品架|试剂位|"
    r"数据线|电源线|连接线|电缆|网线))"
)
_DERIVED_CONFIG_EXCLUDE_TOKENS = (
    "电压",
    "功率",
    "角度",
    "剂量",
    "剂量率",
    "容量",
    "范围",
    "速度",
    "时间",
    "误差",
    "直径",
    "温度",
    "用途",
    "原理",
    "方法",
    "项目",
    "通道",
    "分辨率",
    "污染率",
    "样本位",
    "试剂位",
    "液面探测",
    "流速模式",
    "独立性",
    "功能",
    "要求",
    "性能",
    "兼容规格",
    "控制范围",
    "测试项目",
    "检测原理",
)
_DERIVED_ACTION_CONFIG_MAP = (
    ("自动加样", "自动加样工作站"),
    ("自动加底物", "自动加底物模块"),
    ("自动孵育", "自动孵育模块"),
    ("自动染色", "自动染色模块"),
    ("自动脱色", "自动脱色模块"),
    ("LIS", "LIS接口模块"),
    ("双向传输", "双向传输接口模块"),
    ("可视面板", "可视面板"),
)


def _normalize_derived_config_name(name: str) -> str:
    """清理从技术条款里反推出来的配置项名称。"""
    normalized = _normalize_config_item_name(name)
    normalized = re.sub(r"^[与及和]\s*", "", normalized)
    normalized = re.sub(
        r"^(?:(?:具备|配置|配备|采用|支持|可兼容|可选配|原厂配套的|原厂配套))+",
        "",
        normalized,
    )
    normalized = re.sub(r"[（(]\d+[）)]$", "", normalized)
    normalized = re.sub(r"(相互独立|可同时连续工作或单独分开工作|可同时连续工作|单独分开工作)$", "", normalized)
    normalized = normalized.strip(" ：:，,；;。")
    return normalized


def _is_derived_config_candidate(name: str) -> bool:
    """判断技术条款里的短语是否可以视为配置项。"""
    normalized = _normalize_derived_config_name(name)
    if not normalized:
        return False
    if _is_noise_config_item(normalized):
        return False
    if any(token in normalized for token in _DERIVED_CONFIG_EXCLUDE_TOKENS):
        return False
    return bool(_DERIVED_CONFIG_CANDIDATE_PATTERN.search(normalized)) or any(
        token in normalized
        for token in ("工作站", "软件", "系统", "模块", "组件", "探头", "扫码器", "报警仪", "冷水机", "UPS", "底座", "头托", "头钉", "连接器")
    )


def _explode_derived_config_name(name: str) -> list[str]:
    """把“电泳系统与染色系统”这类复合配置名拆成独立项。"""
    normalized = _normalize_derived_config_name(name)
    if not normalized:
        return []
    parts = [
        _normalize_derived_config_name(part)
        for part in re.split(r"[与及和、]", normalized)
        if _normalize_derived_config_name(part)
    ]
    if len(parts) >= 2 and all(_is_derived_config_candidate(part) for part in parts):
        return parts
    return [normalized]


def _extract_derived_config_names(text: str) -> list[str]:
    """从技术条款文本中提取候选配置项名称。"""
    candidates: list[str] = []
    for match in _DERIVED_CONFIG_CANDIDATE_PATTERN.finditer(_safe_text(text, "")):
        for candidate in _explode_derived_config_name(match.group(1)):
            if _is_derived_config_candidate(candidate):
                candidates.append(candidate)
    normalized_text = _safe_text(text, "")
    for token, mapped_name in _DERIVED_ACTION_CONFIG_MAP:
        if token in normalized_text:
            candidates.append(mapped_name)
    return candidates


def _config_items_need_enrichment(items: list[tuple[str, str, str, str]]) -> bool:
    """判断配置表是否仍然过薄，需要从技术条款补项。"""
    meaningful = [
        item
        for item in items
        if not _is_noise_config_item(item[0], item[3])
        and "待补充" not in item[0]
    ]
    return len(meaningful) < 2


def _derive_config_items_from_technical_requirements(
    pkg: ProcurementPackage,
    tender_raw: str,
    parsed_items: list[tuple[str, str, str, str]],
    seen_names: set[str],
) -> None:
    """当招标文件没有显式装箱明细时，从技术条款反推关键配置项。"""
    def _derived_remark(candidate_name: str, source_key: str, source_val: str) -> str:
        """生成标准化备注，明确标注为反推项并提示核对。"""
        category = _classify_config_item(candidate_name)
        source_text = f"{_safe_text(source_key, '')} {_safe_text(source_val, '')}"

        # 明确标注反推来源，方便人工核对
        purpose = ""
        if "自动" in source_text:
            purpose = "用于实现自动化处理流程"
        elif "独立" in source_text:
            purpose = "用于满足独立运行要求"
        elif "温控" in source_text:
            purpose = "用于温度控制"
        elif "LIS" in source_text or "双向传输" in source_text:
            purpose = "用于数据传输及系统对接"
        elif "观察" in source_text or "可视" in source_text:
            purpose = "用于过程观察"
        else:
            normalized_key = _normalize_config_item_name(source_key)
            if normalized_key:
                purpose = f"满足{normalized_key}要求"

        # 统一格式：分类 + 用途 + 反推标注
        if purpose:
            return f"{category}；{purpose}（由招标条款反推，需与装箱清单核对）"
        return f"{category}（由招标条款反推，需与装箱清单核对）"

    source_pairs: list[tuple[str, str]] = []
    for key, raw_value in (getattr(pkg, "technical_requirements", None) or {}).items():
        if isinstance(raw_value, dict):
            for sub_key, sub_value in raw_value.items():
                source_pairs.append((_safe_text(sub_key, ""), _as_text(sub_value)))
            continue
        source_pairs.append((_safe_text(key, ""), _as_text(raw_value)))

    source_pairs.extend(_effective_requirements(pkg, tender_raw))

    seen_pairs: set[str] = set()
    for key, val in source_pairs:
        pair_signature = f"{key}::{val}"
        if pair_signature in seen_pairs:
            continue
        seen_pairs.add(pair_signature)
        candidate_names: list[str] = []
        for key_name in _explode_derived_config_name(key):
            if _is_derived_config_candidate(key_name):
                candidate_names.append(key_name)
        candidate_names.extend(_extract_derived_config_names(val))

        for candidate_name in candidate_names:
            remark = _derived_remark(candidate_name, key, val)
            _append_config_item(
                parsed_items,
                seen_names,
                name=candidate_name,
                unit=_default_config_unit(candidate_name),
                qty="1",
                remark=remark,
            )


def _extract_configuration_items(
    pkg: ProcurementPackage,
    tender_raw: str,
    *,
    normalized_result: dict[str, Any] | None = None,
) -> list[tuple[str, str, str, str]]:
    """提取配置项。

    优先从 normalized_result 中提取 category='config_requirement' 的条目，
    再用关键词兜底扫描。
    """
    # ── 优先：从归一化结果按 ClauseCategory 过滤 ──
    structured_config = _structured_requirements_for_package(
        normalized_result, pkg.package_id, category_filter="config_requirement",
    ) if normalized_result else []

    parsed_items: list[tuple[str, str, str, str]] = []
    _seen_names: set[str] = set()

    _extract_config_items_from_package_requirements(pkg, parsed_items, _seen_names)

    for req in structured_config:
        name = _normalize_config_item_name(_safe_text(req.get("param_name") or req.get("parameter_name"), ""))
        unit = _safe_text(req.get("unit"), "项")
        qty = _safe_text(req.get("threshold"), "1")
        remark = _classify_config_item(name)
        _append_config_item(
            parsed_items,
            _seen_names,
            name=name,
            unit=unit,
            qty=qty,
            remark=remark,
        )

    # ── 兜底：关键词匹配提取（仅在结构化结果不足时）──
    requirements = _effective_requirements(pkg, tender_raw)

    for key, val in requirements:
        if not any(marker in key for marker in _CONFIG_REQUIREMENT_KEYS):
            continue
        fragments = [frag.strip() for frag in re.split(r"[，,；;、]", val) if frag.strip()]
        for fragment in fragments:
            normalized = _normalize_requirement_line(fragment)
            if not normalized:
                continue
            match = re.match(
                r"^(?P<name>.+?)(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>台|套|个|把|本|件|组|副|支|块|张|台套)?$",
                normalized,
            )
            if match:
                name = _normalize_config_item_name(match.group("name").strip(" ：:"))
                qty = match.group("qty")
                unit = match.group("unit") or "项"
            else:
                name = _normalize_config_item_name(normalized.strip(" ：:"))
                qty = "1"
                unit = "项"

            if not name:
                continue

            remark = _classify_config_item(name)
            _append_config_item(
                parsed_items,
                _seen_names,
                name=name,
                unit=unit,
                qty=qty,
                remark=remark,
            )

    config_scope = _extract_package_configuration_scope_text(pkg, tender_raw)
    if config_scope:
        heading_matches = list(
            re.finditer(
                r"(?:主要配置、功能|主要配置功能|装箱配置单|装箱配置|配置清单|设备配置与配件|设备配置|配置与配件|主要配置|标准配置)[:：]?\s*",
                config_scope,
            )
        )
        if heading_matches:
            config_scope = config_scope[heading_matches[-1].end() :]
        unit_pattern = "|".join(re.escape(unit) for unit in _CONFIG_ITEM_UNITS)
        normalized_scope_lines = [
            re.sub(
                r"^(?:装箱配置单|装箱配置|配置清单|设备配置与配件|设备配置|配置与配件|主要配置|标准配置)[:：]?\s*",
                "",
                _normalize_requirement_line(raw_line),
            ).strip(" ：:;；,，。")
            for raw_line in config_scope.splitlines()
        ]
        normalized_scope_lines = [
            line
            for line in normalized_scope_lines
            if line and not _contains_any(line, _CONFIG_EXIT_HINTS)
        ]

        for raw_line in config_scope.splitlines():
            normalized_line = _normalize_requirement_line(raw_line)
            normalized_line = re.sub(
                r"^(?:装箱配置单|装箱配置|配置清单|设备配置与配件|设备配置|配置与配件|主要配置|标准配置)[:：]?\s*",
                "",
                normalized_line,
            ).strip(" ：:;；,，。")
            if not normalized_line or _contains_any(normalized_line, _CONFIG_EXIT_HINTS):
                continue

            fragments = [frag.strip() for frag in re.split(r"[；;、,，]", normalized_line) if frag.strip()]
            for fragment in fragments:
                normalized = _normalize_requirement_line(fragment)
                if not normalized or _contains_any(normalized, _CONFIG_EXIT_HINTS):
                    continue
                if "|" in normalized:
                    cells = [cell.strip() for cell in normalized.split("|") if cell.strip()]
                    if not cells or any(cell in {"序号", "配置名称", "名称", "数量", "单位", "备注"} for cell in cells):
                        continue
                    if cells and re.fullmatch(r"\d+", cells[0]):
                        cells = cells[1:]
                    normalized = " ".join(cells[:4]).strip()
                if not normalized:
                    continue

                match = re.match(
                    rf"^(?P<name>.+?)(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{unit_pattern})(?:\s|$)",
                    normalized,
                )
                if match:
                    name = _normalize_config_item_name(match.group("name").strip(" ：:"))
                    qty = match.group("qty")
                    unit = match.group("unit")
                else:
                    name = _normalize_config_item_name(normalized.strip(" ：:"))
                    qty = "1"
                    unit = "项"

                if not name or len(name) < 2 or _contains_any(name, _CONFIG_SECTION_HINTS):
                    continue

                remark = _classify_config_item(name)
                _append_config_item(
                    parsed_items,
                    _seen_names,
                    name=name,
                    unit=unit,
                    qty=qty,
                    remark=remark,
                )

        idx = 0
        while idx < len(normalized_scope_lines):
            current = normalized_scope_lines[idx]
            next_line = normalized_scope_lines[idx + 1] if idx + 1 < len(normalized_scope_lines) else ""
            third_line = normalized_scope_lines[idx + 2] if idx + 2 < len(normalized_scope_lines) else ""
            fourth_line = normalized_scope_lines[idx + 3] if idx + 3 < len(normalized_scope_lines) else ""

            if re.fullmatch(r"\d+", current):
                if (
                    next_line
                    and third_line
                    and fourth_line in _CONFIG_ITEM_UNITS
                    and not re.fullmatch(r"\d+(?:\.\d+)?", next_line)
                    and re.fullmatch(r"\d+(?:\.\d+)?", third_line)
                ):
                    name = _normalize_config_item_name(next_line)
                    qty = third_line
                    unit = fourth_line
                    remark = _classify_config_item(name)
                    _append_config_item(
                        parsed_items,
                        _seen_names,
                        name=name,
                        unit=unit,
                        qty=qty,
                        remark=remark,
                    )
                    idx += 4
                    continue
                idx += 1
                continue

            if (
                current
                and next_line
                and third_line in _CONFIG_ITEM_UNITS
                and not re.fullmatch(r"\d+(?:\.\d+)?", current)
                and re.fullmatch(r"\d+(?:\.\d+)?", next_line)
            ):
                name = _normalize_config_item_name(current)
                qty = next_line
                unit = third_line
                remark = _classify_config_item(name)
                _append_config_item(
                    parsed_items,
                    _seen_names,
                    name=name,
                    unit=unit,
                    qty=qty,
                    remark=remark,
                )
                idx += 3
                continue

            idx += 1

        flat_scope = " ".join(
            _normalize_requirement_line(line)
            for line in config_scope.splitlines()
            if _normalize_requirement_line(line)
        )
        flat_scope = re.sub(
            rf"设备总台数\s*[:：;]?\s*\d+(?:\.\d+)?\s*(?:{unit_pattern})",
            " ",
            flat_scope,
        )
        flat_scope = re.sub(r"如有多个配置请另行加行。?", " ", flat_scope)
        flat_scope = re.sub(r"配置\s*\d+\s*[:：]?", " ", flat_scope)
        flat_scope = re.sub(
            rf"(?P<name>[A-Za-z0-9\u4e00-\u9fa5（）()/+.\-\s]{{2,40}}?)\s*(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{unit_pattern})",
            lambda match: (
                f"{_normalize_config_item_name(match.group('name'))}|{match.group('qty')}|{match.group('unit')}||"
            ),
            flat_scope,
        )
        for token in [part for part in flat_scope.split("||") if part.strip()]:
            pieces = [part.strip() for part in token.split("|") if part.strip()]
            if len(pieces) != 3:
                continue
            name, qty, unit = pieces
            remark = _classify_config_item(name)
            _append_config_item(
                parsed_items,
                _seen_names,
                name=name,
                unit=unit,
                qty=qty,
                remark=remark,
            )

    # 当结构化提取完全没有配置项时，从技术条款反推关键配置项作为人工底稿参考。
    # 仅在 parsed_items 为空时启用，避免在已有配置时凭空追加。
    if not parsed_items:
        _derive_config_items_from_technical_requirements(pkg, tender_raw, parsed_items, _seen_names)

    # 智能去重：基于 token Jaccard 相似度而非简单子串匹配
    parsed_items.sort(
        key=lambda item: (
            item[1] == "项",
            item[2] in {"", "1"} and item[1] == "项",
            -len(item[0]),
        )
    )
    deduped: list[tuple[str, str, str, str]] = []
    deduped_token_sets: list[set[str]] = []
    for item in parsed_items:
        norm_name = _normalize_config_item_name(re.sub(r"[\s，,。；;：:]+$", "", item[0]).strip())
        if not norm_name:
            continue
        item_tokens = _config_dedup_tokens(norm_name)
        is_dup = False
        for existing_item, existing_tokens in zip(deduped, deduped_token_sets):
            if not item_tokens or not existing_tokens:
                continue
            intersection = item_tokens & existing_tokens
            union = item_tokens | existing_tokens
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard >= 0.8 and item[1] == existing_item[1]:
                is_dup = True
                break
        if is_dup:
            continue
        deduped.append((norm_name, item[1], item[2], item[3]))
        deduped_token_sets.append(item_tokens)

    # Config pollution cleaning — remove non-config items that leaked through boundary detection
    # 先进行二次服务项过滤，然后再做通用清理
    deduped = [(n, u, q, r) for n, u, q, r in deduped if not _looks_like_service_config_noise(n, r)]
    cleaned = _clean_config_items(deduped, pkg.package_id, pkg.item_name)

    # 完全提取不到任何配置时，尝试从技术条款反推
    if not cleaned:
        _derive_config_items_from_technical_requirements(pkg, tender_raw, deduped, _seen_names)
        # 再次过滤服务项
        deduped = [(n, u, q, r) for n, u, q, r in deduped if not _looks_like_service_config_noise(n, r)]
        cleaned = _clean_config_items(deduped, pkg.package_id, pkg.item_name)

    # 如果仍然提取不到，保留一条占位提示
    if not cleaned:
        cleaned.extend([
            (
                "【待填写：配置清单】",
                "项",
                "1",
                "未从招标文件中提取到配置明细，请根据投标产品实际配置逐项补录",
            ),
        ])

    return cleaned


def _clean_config_items(
    raw_config_items: list[tuple[str, str, str, str]],
    package_id: str = "",
    package_item_name: str = "",
) -> list[tuple[str, str, str, str]]:
    """Config Cleaner: 统一的配置项清理规则。"""

    _CONFIG_POLLUTION_TOKENS = (
        "评分标准", "评分办法", "商务条款", "合同条款", "投标人须知",
        "质保期", "售后服务", "付款方式", "验收标准", "违约责任",
        "评审因素", "评审办法", "评审标准", "投标有效期", "履约保证金",
        "包装要求", "运输要求", "保险要求", "技术参数", "技术要求",
        "采购需求", "性能要求", "参数要求",
    )

    _TABLE_HEADER_TOKENS = (
        "序号", "配置名称", "名称", "数量", "单位", "备注", "说明", "规格",
        "品牌", "型号", "产地", "价格", "小计", "合计", "货物名称",
        "配置项", "分类", "用途", "功能描述", "配置",
    )

    _TEMPLATE_STATEMENT_PATTERNS = (
        "按招标文件", "按采购文件", "详见招标文件", "详见采购文件",
        "见附件", "见清单", "参见", "如下", "以下",
        "按招标文件配置要求", "详见技术要求", "按采购需求",
    )

    _SCAFFOLD_NOISE_TOKENS = (
        "如有多个",
        "配置请另行加行",
        "请另行加行",
        "可按实际增减",
        "模板",
        "示例",
    )

    cleaned: list[tuple[str, str, str, str]] = []
    seen_token_sets: list[set[str]] = []
    forbidden_terms = _package_forbidden_terms(package_item_name) if package_item_name else set()

    for item in raw_config_items:
        name, unit, qty, remark = item
        name = (name or "").strip()

        # 规则1: 过滤表头/空名/纯模板名
        if not name or name in _TABLE_HEADER_TOKENS:
            continue

        # 规则2: 过滤模板说明行
        if any(pattern in name for pattern in _TEMPLATE_STATEMENT_PATTERNS):
            continue

        # 规则3: 过滤支架说明语/脚手架噪音
        if any(token in name for token in _SCAFFOLD_NOISE_TOKENS):
            continue

        # 规则4: 过滤污染内容
        if any(token in name for token in _CONFIG_POLLUTION_TOKENS):
            continue

        # 规则5: 过滤设备串包污染
        if forbidden_terms and any(token in name for token in forbidden_terms):
            continue

        # 规则6: 过滤跨包污染（显式写了其他包号）
        if package_id:
            other_package_pattern = r"包\s*(\d+)"
            matches = re.findall(other_package_pattern, name)
            if matches and all(match != package_id for match in matches):
                continue

        # 规则7: 最小长度检查
        if len(name) < 2:
            continue

        # 规则8: 纯数字或纯符号
        if re.fullmatch(r"[\d\s\-_]+", name) or re.fullmatch(r"[^\u4e00-\u9fa5\w]+", name):
            continue

        # 规则9: 二次 Jaccard 去重
        item_tokens = {t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", name.strip()) if len(t) >= 2}
        is_dup = False
        for existing_tokens in seen_token_sets:
            if not item_tokens or not existing_tokens:
                continue
            intersection = item_tokens & existing_tokens
            union = item_tokens | existing_tokens
            if union and len(intersection) / len(union) >= 0.85:
                is_dup = True
                break
        if is_dup:
            continue

        cleaned.append((name, unit, qty, remark))
        seen_token_sets.append(item_tokens)

    return cleaned


def _profile_config_items(product_profile: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    """从产品 profile 提取配置项，并用 technical_specs / evidence_refs 做补强。"""
    if not product_profile:
        return []

    structured_items = product_profile.get("config_items") or []
    parsed: list[tuple[str, str, str, str]] = []

    for item in structured_items:
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("配置项") or item.get("name") or item.get("item_name") or item.get("名称"))
        if not name:
            continue
        unit = _as_text(item.get("单位") or item.get("unit") or "项")
        qty = _as_text(item.get("数量") or item.get("qty") or item.get("quantity") or "1")
        desc = _as_text(item.get("说明") or item.get("description") or item.get("remark") or "")
        remark = _classify_config_item(name)
        if desc:
            remark = f"{remark}；{desc}"
        parsed.append((name, unit, qty, remark))

    tech_specs = product_profile.get("technical_specs") or {}
    existing_names = {item[0].strip() for item in parsed}
    config_hint_keys = (
        "配置", "配备", "含", "包含", "包括", "标配", "选配", "附件",
        "主机", "模块", "工作站", "软件", "显示器", "打印机", "数据线",
        "电源线", "支架", "台车", "说明书", "合格证", "保修卡", "装箱单",
        "冷水机", "UPS", "接口模块", "分析软件", "探头", "扫码器",
    )

    for key, val in tech_specs.items():
        key_text = _as_text(key)
        val_text = _as_text(val)
        if not key_text:
            continue
        if any(hint in key_text for hint in config_hint_keys) or any(hint in val_text for hint in config_hint_keys):
            if key_text.strip() not in existing_names:
                parsed.append((key_text, "项", "1", f"{_classify_config_item(key_text)}；{val_text or '待核对'}"))
                existing_names.add(key_text.strip())

    evidence_refs = product_profile.get("evidence_refs") or []
    for ref in evidence_refs:
        if not isinstance(ref, dict):
            continue
        file_name = _as_text(ref.get("file_name", ""))
        if file_name and file_name not in existing_names and any(k in file_name for k in ("说明书", "装箱", "配置", "清单", "彩页")):
            parsed.append((file_name, "份", "1", "随机文件"))
            existing_names.add(file_name)

    return _clean_config_items(parsed)

def _is_main_device_item(name: str, pkg: ProcurementPackage, product: Any = None) -> bool:
    """判断配置项是否属于主设备。"""
    text = _as_text(name).strip()
    if not text:
        return False

    candidates = {
        _as_text(pkg.item_name).strip(),
        _as_text(getattr(product, "product_name", "")).strip() if product else "",
        _as_text(getattr(product, "model", "")).strip() if product else "",
    }
    candidates = {c for c in candidates if c}

    if any(c and (c in text or text in c) for c in candidates):
        return True

    return any(k in text for k in ("主机", "整机", "设备主机", "分析仪主机"))

def _build_configuration_table(
    pkg: ProcurementPackage,
    tender_raw: str,
    product: Any = None,
    *,
    product_profile: dict[str, Any] | None = None,
    normalized_result: dict[str, Any] | None = None,
) -> str:
    """构建双层配置表：第一层配置明细表 + 第二层配置功能描述表。"""
    package_qty = str(int(getattr(pkg, "quantity", 1) or 1))

    # ── 第一层：详细配置明细表（含是否标配、用途说明）──
    lines = [
        "### （二-A）详细配置明细表",
        "| 序号 | 配置名称 | 单位 | 数量 | 是否标配 | 用途说明 | 备注 |",
        "|---:|---|---|---:|---|---|---|",
    ]

    # Build product identity header if product is available
    product_identity_lines: list[str] = []
    if product is not None:
        p_name = _as_text(getattr(product, "product_name", ""))
        p_model = _as_text(getattr(product, "model", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        p_origin = _as_text(getattr(product, "origin", ""))
        if p_name or p_model or p_mfr:
            identity_parts = []
            if p_mfr:
                identity_parts.append(f"品牌/厂家：{p_mfr}")
            if p_model:
                identity_parts.append(f"型号：{p_model}")
            if p_origin:
                identity_parts.append(f"产地：{p_origin}")
            product_identity_lines.append(
                f"| 1 | {p_name or pkg.item_name}主机 | 台 | {package_qty} | 是 | 核心设备主机 | {'；'.join(identity_parts) if identity_parts else '核心设备'} |"
            )

    config_items = _profile_config_items(product_profile)
    if not config_items:
        config_items = _extract_configuration_items(pkg, tender_raw, normalized_result=normalized_result)

    if not config_items:
        inferred_items: list[tuple[str, str, str, str]] = []
        for key, value in (pkg.technical_requirements or {}).items():
            k = _as_text(key)
            v = _as_text(value)
            if any(h in f"{k} {v}" for h in
                   ("模块", "工作站", "软件", "接口", "探头", "系统", "附件", "冷水机", "UPS", "扫码器")):
                inferred_items.append((k, "项", "1", f"{_classify_config_item(k)}；由招标条款反推，请与装箱清单核对"))
        config_items = _clean_config_items(inferred_items)

    if not config_items and not product_identity_lines:
        lines.extend(
            [
                "| 1 | 【待填写：配置清单】 | 项 | 1 | 待确认 | 待确认 | 未从招标文件中提取到配置明细，请根据投标产品实际配置逐项补录 |",
            ]
        )
        return "\n".join(lines)

    idx = 1
    if product_identity_lines:
        lines.extend(product_identity_lines)
        idx = 2

    # 收集配置项描述信息，用于第二层
    config_descriptions: list[tuple[str, str, str]] = []  # (name, usage, remark)
    seen_main_device = bool(product_identity_lines)
    for name, unit, qty, remark in config_items:
        if _looks_like_service_config_noise(name, remark):
            continue
        matched_spec = _fuzzy_spec_lookup(product, name) if product else ""
        usage = _infer_config_usage(name)
        is_standard = "是" if _is_standard_config(name) else "选配"
        if _is_main_device_item(name, pkg, product):
            if seen_main_device:
                continue
            seen_main_device = True
            qty = package_qty
            unit = "台"
        elif _classify_config_item(name) == "核心模块":
            qty = package_qty

        if matched_spec:
            remark_full = f"{remark}；投标产品：{matched_spec}"
        else:
            remark_full = remark

        lines.append(f"| {idx} | {_markdown_cell(name)} | {unit} | {qty} | {is_standard} | {usage} | {remark_full} |")
        config_descriptions.append((name, usage, remark_full))
        idx += 1

    # 备注列已经包含【待填写】/【待补证】占位，
    # 不再重复生成“配置补充说明”，避免每包都出现一段解释性文字，
    # 增加人工删改成本。
    return "\n".join(lines)


def _is_standard_config(name: str) -> bool:
    """判断配置项是否为标配。"""
    n = name.strip()
    if any(k in n for k in ("主机", "整机", "仪器", "设备", "分析仪", "检测仪", "说明书", "合格证", "电源线")):
        return True
    if any(k in n for k in ("选配", "可选", "升级", "扩展")):
        return False
    return True


def _infer_config_usage(name: str) -> str:
    """推断配置项的用途。"""
    n = name.strip()

    if any(k in n for k in ("主机", "整机", "检测主机", "分析主机", "检测单元", "核心模块")):
        return "核心检测/分析设备"

    if any(k in n for k in ("软件", "程序", "平台", "分析软件", "应用软件", "工作站软件")):
        return "数据采集/分析/管理"

    if any(k in n for k in ("试剂", "耗材", "微球", "清洗液", "鞘液", "流式管", "滤芯", "墨盒")):
        return "首批运行耗材"

    if any(k in n for k in ("说明书", "文件", "手册", "合格证", "彩页", "装箱单", "保修卡")):
        return "随机文件"

    if any(k in n for k in ("安装", "培训", "调试", "服务")):
        return "安装/培训资料"

    if any(k in n for k in ("显示器", "打印机", "稳压电源", "UPS", "支架", "台车", "电源线", "数据线")):
        return "配套附件"

    return "配套附件"


def _infer_config_role(name: str, product_name: str) -> str:
    """推断配置项在方案中的角色。"""
    n = name.strip()

    if any(k in n for k in ("显示器",)):
        return "用于显示采集与分析界面"

    if any(k in n for k in ("打印机",)):
        return "用于输出检测结果或报告"

    if any(k in n for k in ("稳压电源", "UPS")):
        return "用于供电保护和设备稳定运行"

    if any(k in n for k in ("软件", "程序", "平台", "分析软件", "应用软件")):
        return "用于数据采集、分析与管理"

    if any(k in n for k in ("说明书", "手册", "合格证", "装箱单", "保修卡")):
        return "用于交付、验收、操作和留档"

    if any(k in n for k in ("试剂", "耗材", "微球", "流式管")):
        return "用于试机、质控或首批运行"

    if any(k in n for k in ("主机", "整机", "检测主机", "分析主机")):
        return f"作为{product_name}核心检测单元"

    return "详见配置清单和产品资料"

def _build_main_parameter_table(
    pkg: ProcurementPackage,
    tender_raw: str,
    product: Any = None,
    *,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profile: dict[str, Any] | None = None,
) -> str:
    """构建主参数表。"""
    def _guess_source_hint(text: str) -> str:
        """推断来源提示文本。"""
        t = _safe_text(text, "")
        if any(k in t for k in ("注册证", "备案凭证", "说明书", "标签", "授权文件")):
            return "注册证/备案凭证/说明书/标签/授权文件"
        if any(k in t for k in ("LIS", "分析软件", "功能截图", "双向传输")):
            return "软件说明书/功能截图/厂家说明"
        if any(k in t for k in ("室间质评", "能力验证", "检测报告", "质控品", "临检中心")):
            return "室间质评报告/能力验证报告/检测报告"
        return "产品说明书/彩页/厂家参数表"

    def _has_real_evidence(row: dict[str, Any]) -> bool:
        """判断是否存在有效证据。"""
        return bool(_safe_text(row.get("bidder_evidence"), "")) or bool(
            _safe_text(row.get("bidder_evidence_source"), "")
        ) or bool(_safe_text(row.get("evidence_ref"), ""))

    requirement_rows, _ = _build_requirement_rows(
        pkg,
        tender_raw,
        product=product,
        normalized_result=normalized_result,
        evidence_result=evidence_result,
        product_profile=product_profile,
    )

    technical_rows: list[dict[str, Any]] = []
    for row in requirement_rows:
        row_pkg = _safe_text(row.get("package_id"), pkg.package_id)
        if row_pkg and str(row_pkg) != str(pkg.package_id):
            continue

        key = _safe_text(row.get("key"), "")
        req = _safe_text(row.get("requirement"), "")

        bad_scaffold_hints = (
            "如有多个",
            "配置请另行加行",
            "我院设备的技术参数与性能要求的基本格式",
            "此栏填“国际”或“国内”",
            "此栏填“国际”或",
        )
        row_text = f"{key} {req}"
        if any(tok in row_text for tok in bad_scaffold_hints):
            continue

        inferred = _classify_clause_category(key, req)
        if inferred != ClauseCategory.technical_requirement:
            continue

        tender_quote = _safe_text(row.get("tender_quote"), "")
        bidder_quote = _safe_text(row.get("bidder_evidence"), "")

        if not _row_is_usable_for_package(
            pkg,
            key,
            req,
            tender_quote=tender_quote,
            bidder_quote=bidder_quote,
        ):
            continue

        bad_tail_hints = ("履约保证金", "付款方式", "交货期", "投标报价", "报价书")
        if any(tok in tender_quote for tok in bad_tail_hints):
            continue

        row = dict(row)
        row["category"] = ClauseCategory.technical_requirement.value
        technical_rows.append(row)

    real_response_count = sum(1 for r in technical_rows if r.get("has_real_response"))
    total_rows = len(technical_rows)
    coverage = real_response_count / max(total_rows, 1)

    if total_rows == 0 or coverage < 0.6:
        lines = [
            f"### 包{pkg.package_id}：{pkg.item_name}",
            "| 序号 | 待补条款 | 招标要求 | 建议证据来源 | 回填位置 |",
            "|---:|---|---|---|---|",
        ]

        unresolved_rows = technical_rows or requirement_rows
        idx = 1
        for row in unresolved_rows:
            key = _safe_text(
                row.get("key") or row.get("requirement_name"),
                "核心参数",
            )
            req = _safe_text(
                row.get("requirement") or row.get("value") or row.get("requirement_value"),
                "详见招标文件",
            )
            has_resp = bool(row.get("has_real_response"))
            has_ev = _has_real_evidence(row)

            if has_resp and has_ev:
                continue

            lines.append(
                f"| {idx} | {_markdown_cell(key)} | {_markdown_cell(req)} | "
                f"{_guess_source_hint(key + ' ' + req)} | 第三章对应包《技术偏离及详细配置明细表》 |"
            )
            idx += 1
            if idx > 20:
                break

        if idx == 1:
            lines.append(
                "| 1 | 核心参数 | 已形成结构化框架 | 产品说明书/彩页/厂家参数表 | 第三章对应包《技术偏离及详细配置明细表》 |"
            )

        return "\n".join(lines)

    lines = [
        f"### 包{pkg.package_id}：{pkg.item_name}",
        "| 序号 | 技术参数项 | 招标要求 | 响应情况 | 备注 |",
        "|---:|---|---|---|---|",
    ]

    for idx, row in enumerate(technical_rows, start=1):
        response_text = _safe_text(row.get("response"), "")
        if (
            not response_text
            or "待填写" in response_text
            or "待核实" in response_text
        ):
            response_text = "待按产品说明书/厂家参数表逐条回填"

        note = _normalize_main_param_note(
            row.get("deviation_status"),
            bool(row.get("has_real_response")),
        )
        lines.append(
            f"| {idx} | {_markdown_cell(str(row['key']))} | {_markdown_cell(str(row['requirement']))} | "
            f"{_markdown_cell(response_text)} | {note} |"
        )

    return "\n".join(lines)

def _build_response_checklist_table(
    pkg: ProcurementPackage,
    mapped_count: int,
    total_requirements: int,
    requirement_rows: list[dict[str, Any]] | None = None,
) -> str:
    """构建响应核对表。"""
    real_response_count = 0
    high_mapping_count = 0
    weak_mapping_count = 0

    if requirement_rows:
        real_response_count = sum(1 for r in requirement_rows if r.get("has_real_response"))
        high_mapping_count = sum(1 for r in requirement_rows if r.get("mapping_confidence") == "high")
        weak_mapping_count = sum(1 for r in requirement_rows if r.get("mapping_confidence") == "weak")

    if total_requirements <= 0:
        evidence_result = (
            f"高置信映射 {high_mapping_count}/{total_requirements} 项，"
            f"弱映射 {weak_mapping_count} 项，"
            f"其余待人工补映射/补证"
        )
        evidence_status = "待补证"
        param_conclusion = "未提取到结构化参数，待人工补充"
        param_status = "待补实参"
    elif real_response_count == total_requirements:
        evidence_result = (
            f"高置信映射 {high_mapping_count}/{total_requirements} 项，"
            f"弱映射 {weak_mapping_count} 项，"
            f"其余待人工补映射/补证"
        )
        evidence_status = "已完成"
        param_conclusion = f"已证实 {real_response_count}/{total_requirements} 项，全部已填入投标产品实参"
        param_status = "已完成"
    elif real_response_count > 0:
        evidence_result = (
            f"高置信映射 {high_mapping_count}/{total_requirements} 项，"
            f"弱映射 {weak_mapping_count} 项，"
            f"其余待人工补映射/补证"
        )
        evidence_status = "部分完成"
        param_conclusion = f"已证实 {real_response_count}/{total_requirements} 项，其余 {total_requirements - real_response_count} 项待补实参"
        param_status = "部分完成"
    else:
        evidence_result = (
            f"高置信映射 {high_mapping_count}/{total_requirements} 项，"
            f"弱映射 {weak_mapping_count} 项，"
            f"其余待人工补映射/补证"
        )
        evidence_status = "待补证"
        param_conclusion = "已形成逐条响应框架，待填入投标产品实参"
        param_status = "待补实参"

    lines = [
        "### （三）技术响应检查清单",
        "| 序号 | 校验项 | 响应结论 | 证据载体 | 校验状态 |",
        "|---:|---|---|---|---|",
        f"| 1 | 关键技术参数逐条响应 | {param_conclusion} | 技术偏离表 | {param_status} |",
        "| 2 | 配置清单完整性 | 已按招标文件配置项展开列示，待匹配投标型号 | 配置明细表 | 待复核 |",
        "| 3 | 交付与培训要求 | 已保留响应框架，待结合投标方案复核 | 报价书与服务方案 | 待复核 |",
        "| 4 | 质保与售后要求 | 已保留服务承诺框架，待补投标方细节 | 售后服务方案 | 待复核 |",
        f"| 5 | 技术条款证据映射 | {evidence_result} | 技术条款证据映射表 | {evidence_status} |",
    ]
    return "\n".join(lines)


def _build_evidence_mapping_table(
    pkg: ProcurementPackage,
    requirement_rows: list[dict[str, Any]],
    total_requirements: int,
) -> str:
    """构建证据映射表。"""
    lines = [
        "### （四）技术条款证据映射表",
        "| 序号 | 技术参数项 | 映射状态 | 证据来源 | 原文片段 | 应用位置 |",
        "|---:|---|---|---|---|---|",
    ]

    if not requirement_rows:
        lines.append("| 1 | 核心技术参数 | 结构化解析结果 / 待补投标方证据 | 未提取到可映射原文片段，需人工复核原文并补齐投标方证据 | 技术偏离表第1行 |")
        return "\n".join(lines)

    for idx, row in enumerate(requirement_rows, start=1):
        mapping_conf = _safe_text(row.get("mapping_confidence"), "none")
        status_text = {
            "high": "精确命中",
            "weak": "弱命中待复核",
            "none": "未命中",
        }.get(mapping_conf, "未命中")

        has_real = row.get("has_real_response", False)
        bidder_ev = _safe_text(row.get("bidder_evidence"), "")
        bidder_source = _safe_text(row.get("bidder_evidence_source"), "")
        tender_quote = _safe_text(row.get("tender_quote"), "")
        bidder_page = row.get("bidder_evidence_page")

        if has_real and bidder_ev:
            page_text = f"（第{bidder_page}页）" if bidder_page is not None else ""
            source_text = _markdown_cell(bidder_source or "投标方资料")
            quote_text = _markdown_cell(bidder_ev) + page_text
            if mapping_conf == "high" and tender_quote:
                quote_text = f"{quote_text}；{_markdown_cell(tender_quote)}"
        else:
            source_text = _markdown_cell("待补投标方证据")
            quote_text = _markdown_cell(tender_quote) if mapping_conf in {"high", "weak"} else ""

        lines.append(
            f"| {idx} | {_markdown_cell(str(row['key']))} | {status_text} | {source_text} | "
            f"{quote_text or ' '} | 技术偏离表第{idx}行 |"
        )

    if total_requirements > len(requirement_rows):
        lines.append("|  | 其余参数项 | 招标原文 / 待补投标方证据 | 详见延伸条款，需人工补充映射与投标方证据 | 技术偏离表后续行 |")

    return "\n".join(lines)
