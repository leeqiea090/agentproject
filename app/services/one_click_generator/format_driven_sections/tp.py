"""竞争性谈判格式驱动章节生成。"""
from __future__ import annotations

import re

from app.schemas import BidDocumentSection
from app.services.one_click_generator.config_tables import _build_configuration_table
from app.services.one_click_generator.response_tables import (
    _build_deviation_table,
    _build_requirement_rows,
    _has_real_bidder_response,
)
from .common import _clean_text, _md_table, _truncate_commercial_tail

_TP_APPENDIX_TITLES = [
    "附一、资格性审查响应对照表",
    "附二、符合性审查响应对照表",
    "附三、详细评审响应对照表",
    "附四、投标无效情形汇总及自检表",
]

_TP_TEMPLATE_PATTERNS: list[tuple[str, str]] = [
    ("一、响应文件封面格式", r"一\s*、\s*响应文件封面格式"),
    ("二、报价书", r"二\s*、\s*报价书"),
    ("三、报价一览表", r"三\s*、\s*报价一览表"),
    ("四、资格承诺函", r"四\s*、\s*资格承诺函"),
    ("五、技术偏离及详细配置明细表", r"五\s*、\s*技术偏离及详细配置明细表"),
    ("六、技术服务和售后服务的内容及措施", r"六\s*、\s*技术服务和售后服务的内容及措施"),
    ("七、法定代表人/单位负责人授权书", r"七\s*、\s*法定代表人\s*/\s*单位负责人授权书"),
    ("八、法定代表人/单位负责人和授权代表身份证明", r"八\s*、\s*法定代表人\s*/\s*单位负责人和授权代表身份证明"),
    ("九、小微企业声明函", r"九\s*、\s*小微企业声明函"),
    ("十、残疾人福利性单位声明函", r"十\s*、\s*残疾人福利性单位声明函"),
    ("十一、投标人关联单位的说明", r"十一\s*、\s*投标人关联单位的说明"),
]

_TP_COMPLIANCE_LABELS = [
    "投标报价",
    "投标文件规范性、符合性",
    "主要商务条款",
    "联合体投标",
    "技术部分实质性内容",
    "其他要求",
]

_TP_SERVICE_INCLUDE_KEYWORDS = (
    "售后",
    "维修",
    "维保",
    "保修",
    "上门",
    "响应时间",
    "培训",
    "安装调试",
    "验收",
    "巡检",
    "技术支持",
    "配件库",
    "升级",
    "LIS 对接费用",
    "维护保养",
)

_TP_SERVICE_EXCLUDE_KEYWORDS = (
    "设备名称",
    "检测原理",
    "检测方法",
    "激光器",
    "检测通道",
    "通道",
    "FITC",
    "PE",
    "APC",
    "CV",
    "光源输出",
    "试剂位",
    "样本位",
    "分析速度",
    "检测速度",
    "参数阈值",
)

def _slice_service_tail(text: str) -> str:
    """截取服务tail。"""
    normalized = _clean_text(text)
    if not normalized:
        return ""
    markers = ("维修", "维保", "保修", "响应", "培训", "安装", "调试", "验收", "售后", "巡检", "升级", "维护")
    positions = [normalized.find(m) for m in markers if normalized.find(m) >= 0]
    if not positions:
        return normalized
    return normalized[min(positions):].strip()


def _is_tp_service_or_acceptance_clause(text: str) -> bool:
    """判断TP 格式服务or验收条款。"""
    normalized = _clean_text(text)
    if not normalized:
        return False
    if any(keyword in normalized for keyword in _TP_SERVICE_EXCLUDE_KEYWORDS):
        return False
    if "LIS" in normalized and "费用" in normalized:
        return True
    return any(keyword in normalized for keyword in _TP_SERVICE_INCLUDE_KEYWORDS)


def _norm_header(text: str) -> str:
    """规范化表头文本。"""
    return "".join(str(text or "").split())


def _tidy_extracted_text(text: str) -> str:
    """清理抽取后的文本，合并 PDF 碎片化换行。"""
    value = _clean_text(text)
    if not value:
        return ""
    # 合并中文之间被空格打断的文本
    value = re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "", value)
    value = re.sub(r"(?<=[一-鿿])\s+(?=[□☑☐（《“])", "", value)
    value = re.sub(r"(?<=[□☑☐）》”。，；：、])\s+(?=[一-鿿])", "", value)
    value = re.sub(r"(?<=[（《“])\s+", "", value)
    value = re.sub(r"\s+(?=[）》。；：，、”])", "", value)
    value = re.sub(r"(?<=[：，。；、”])\s+", "", value)
    return value.strip()


def _normalize_tp_procurement_terms(text: str) -> str:
    """统一 TP 文本中的采购方式术语，避免串入磋商用语。"""
    value = _tidy_extracted_text(text)
    if not value:
        return ""
    replacements = (
        ("竞争性磋商文件", "竞争性谈判文件"),
        ("磋商文件", "谈判文件"),
        ("磋商小组", "谈判小组"),
        ("磋商过程", "谈判过程"),
        ("磋商依据", "谈判依据"),
    )
    for source, target in replacements:
        value = value.replace(source, target)
    return value


def _tpl_header_titles(tpl) -> list[str]:
    """返回模板中的表头标题列表。"""
    columns = list(getattr(tpl, "columns", None) or [])
    headers = [_clean_text(getattr(col, "title", "")) for col in columns]
    return [header for header in headers if header]


def _is_valid_tp_header_set(attr_name: str, headers: list[str]) -> bool:
    """校验 TP 评审表头是否可直接复用，避免误用脏表头。"""
    normalized = [_norm_header(header) for header in headers if _norm_header(header)]
    if not normalized or "序号" not in normalized[0]:
        return False
    if any(len(header) > 24 or any(token in header for token in ("。", "；", "：")) for header in normalized):
        return False

    required_groups = {
        "qualification_review_table": [
            ("审查项", "审查内容"),
            ("招标文件要求", "采购文件要求", "合格条件"),
        ],
        "compliance_review_table": [
            ("审查项", "审查内容"),
            ("招标文件要求", "采购文件要求", "合格条件"),
        ],
        "detailed_review_table": [
            ("评审项", "评审因素", "评分项目", "内容"),
            ("采购文件评分要求", "评分要求", "评审标准", "评分标准"),
        ],
        "invalid_bid_table": [
            ("无效情形",),
            ("自检结果", "是否满足", "结果"),
        ],
    }
    groups = required_groups.get(attr_name, [])
    return all(any(any(token in header for token in group) for header in normalized) for group in groups)


def _select_tp_headers(tender, attr_name: str, fallback_headers: list[str]) -> list[str]:
    """选择TP 格式表头。"""
    tpl = getattr(tender, attr_name, None)
    headers = _tpl_header_titles(tpl)
    if _is_valid_tp_header_set(attr_name, headers):
        return headers
    return fallback_headers


def _render_tp_row(
    headers: list[str],
    *,
    seq: str,
    item_name: str = "",
    requirement: str = "",
    response_placeholder: str = "【待填写：对应材料名称/页码】",
    status_placeholder: str = "【待填写：满足/不满足】",
    note_placeholder: str = "【待填写】",
    evidence_placeholder: str = "【待填写：页码】",
    invalid_item: str = "",
    self_check_placeholder: str = "【待填写：符合/不符合】",
) -> list[str]:
    """渲染TP 格式行。"""
    row: list[str] = []
    for header in headers:
        norm = _norm_header(header)
        if "序号" in norm:
            row.append(seq)
        elif any(token in norm for token in ("审查项", "审查内容", "评审项", "评审因素", "条款名称")):
            row.append(item_name)
        elif any(token in norm for token in ("招标文件要求", "采购文件要求", "合格条件", "评分要求", "评审标准")):
            row.append(requirement)
        elif "无效情形" in norm:
            row.append(invalid_item)
        elif any(token in norm for token in ("响应文件对应内容", "投标文件内容", "响应内容", "响应情况")):
            row.append(response_placeholder)
        elif "自评说明" in norm:
            row.append(status_placeholder)
        elif any(token in norm for token in ("证明材料", "证据材料", "证明文件")):
            row.append(evidence_placeholder)
        elif "页码" in norm:
            row.append(evidence_placeholder)
        elif any(token in norm for token in ("是否满足", "是否响应")):
            row.append(status_placeholder)
        elif "自检结果" in norm:
            row.append(self_check_placeholder)
        elif "备注" in norm:
            row.append(note_placeholder)
        else:
            row.append("")
    return row


def _normalize_dense_text(text: str) -> str:
    """归一化紧凑文本，便于匹配。"""
    text = text or ""
    text = re.sub(r"-\s*第\s*\d+\s*页\s*-", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _dedupe_consecutive_lines(lines: list[str]) -> list[str]:
    """去除连续重复的文本行。"""
    result: list[str] = []
    previous = ""
    for raw in lines:
        line = raw.strip()
        if not line or line == previous:
            continue
        result.append(line)
        previous = line
    return result


def _looks_like_tp_title_fragment(line: str, title: str) -> bool:
    """判断likeTP 格式标题fragment。"""
    compact = _clean_text(line)
    if not compact:
        return False
    if any(token in compact for token in ("签字", "公章", "复印件", "授权", "电话", "地址", "邮编", "传真")):
        return False

    normalized_line = re.sub(r"[\s#`*:：、（）()/_\-—]", "", compact)
    if len(normalized_line) < 4:
        return False

    for candidate, _ in _TP_TEMPLATE_PATTERNS:
        normalized_title = re.sub(r"[\s#`*:：、（）()/_\-—]", "", candidate)
        if normalized_line == normalized_title:
            return True
        if len(normalized_line) <= 14 and normalized_line in normalized_title:
            return True
    current_title = re.sub(r"[\s#`*:：、（）()/_\-—]", "", title)
    return len(normalized_line) <= 14 and normalized_line in current_title


def _clean_tp_template_block(block: str, title: str) -> str:
    """清理TP 格式模板文本块，合并 PDF 碎片化换行。"""
    body = re.sub(r"-\s*第\s*\d+\s*页\s*-", "", block or "").strip()
    if not body:
        return ""

    title_pat = next((pat for key, pat in _TP_TEMPLATE_PATTERNS if key == title), None)
    if title_pat:
        body = re.sub(rf"^\s*(?:{title_pat})\s*", "", body, count=1)
        body = re.sub(rf"^\s*(?:{title_pat})\s*", "", body, count=1)

    lines = _dedupe_consecutive_lines(body.splitlines())
    cleaned: list[str] = []
    for line in lines:
        compact = _clean_text(line)
        if not compact or compact == _clean_text(title):
            continue
        if compact.startswith("第 ") and compact.endswith(" 页"):
            continue
        if _looks_like_tp_title_fragment(line, title):
            continue
        cleaned.append(line.strip())

    # 合并 PDF 碎片化短行：将过短且不以句末标点结尾的行拼接到前一行
    merged: list[str] = []
    for line in cleaned:
        if (
            merged
            and len(line) <= 8
            and not re.match(r"^(?:[一二三四五六七八九十]+[、.]|\d+[、.）)]|[★▲■●※]|#+\s|\|)", line)
            and not line.endswith(("。", "；", "："))
        ):
            last_char = merged[-1][-1] if merged[-1] else ""
            first_char = line[0] if line else ""
            cjk_last = "\u4e00" <= last_char <= "\u9fff" or last_char in "\uff0c\uff1b\uff1a\u3001\uff09\u3011\u300d\u300b\u25a1\u2611\u2610"
            cjk_first = "\u4e00" <= first_char <= "\u9fff" or first_char in "\uff08\u3010\u300c\u300a\u25a1\u2611\u2610"
            if cjk_last or cjk_first:
                merged[-1] += line
            else:
                merged[-1] += " " + line
        else:
            merged.append(line)
    return "\n".join(merged).strip()


def _extract_tp_format_block(tender_raw: str) -> str:
    """提取TP 格式格式块。"""
    text = tender_raw or ""
    if not text:
        return ""

    chapter_pat = re.compile(r"(?:^|\n)\s*第六章(?:\s*第六章)?\s*响应文件格式(?:与要求)?", re.M)
    match = chapter_pat.search(text)
    if not match:
        return ""

    tail = text[match.start():]
    stop = re.search(r"(?:^|\n)\s*第[七八九十]\s*章", tail, re.M)
    return tail[:stop.start()].strip() if stop else tail.strip()


def _extract_tp_template_blocks(tender_raw: str) -> dict[str, str]:
    """提取TP 格式模板文本块。"""
    block = _extract_tp_format_block(tender_raw)
    if not block:
        return {}

    hits: list[tuple[str, int]] = []
    cursor = 0
    for title, pat in _TP_TEMPLATE_PATTERNS:
        match = re.search(pat, block[cursor:], re.S)
        if not match:
            continue
        start = cursor + match.start()
        hits.append((title, start))
        cursor = start + 1

    result: dict[str, str] = {}
    for idx, (title, start) in enumerate(hits):
        end = hits[idx + 1][1] if idx + 1 < len(hits) else len(block)
        raw = block[start:end].strip()
        cleaned = _clean_tp_template_block(raw, title)
        if cleaned:
            result[title] = cleaned
    return result


def _build_tp_template_section(tender_raw: str, title: str, fallback: str) -> str:
    """构建TP 格式模板章节。"""
    blocks = _extract_tp_template_blocks(tender_raw)
    return blocks.get(title, fallback).strip()


def _extract_tp_review_block(tender_raw: str, anchor_patterns: list[str], stop_patterns: list[str]) -> str:
    """提取TP 格式评审块。"""
    text = _normalize_dense_text(tender_raw)
    if not text:
        return ""

    start = None
    for pat in anchor_patterns:
        match = re.search(pat, text)
        if match:
            start = match.start()
            break

    if start is None:
        return ""

    tail = text[start:]
    stop_pos = None
    for pat in stop_patterns:
        match = re.search(pat, tail)
        if match and match.start() > 0:
            if stop_pos is None or match.start() < stop_pos:
                stop_pos = match.start()
    return tail[:stop_pos].strip() if stop_pos is not None else tail.strip()


def _extract_tp_contract_package_block(block: str, package_id: str) -> str:
    """提取TP 格式contract包件文本块。"""
    text = block or ""
    if not text:
        return ""

    start_pat = re.compile(rf"合同包\s*{re.escape(str(package_id))}\s*[（(]")
    match = start_pat.search(text)
    if not match:
        return ""

    tail = text[match.start():]
    next_match = re.search(r"合同包\s*\d+\s*[（(]", tail[len(match.group(0)):])
    if next_match:
        return tail[: len(match.group(0)) + next_match.start()].strip()
    return tail.strip()


def _extract_tp_named_segments(text: str, markers: list[str]) -> list[tuple[str, str]]:
    """提取TP 格式namedsegments。"""
    block = text or ""
    hits: list[tuple[str, int]] = []
    cursor = 0
    for marker in markers:
        pat = re.compile(re.escape(marker))
        match = pat.search(block, cursor)
        if not match:
            continue
        hits.append((marker, match.start()))
        cursor = match.start() + 1

    segments: list[tuple[str, str]] = []
    for idx, (marker, start) in enumerate(hits):
        end = hits[idx + 1][1] if idx + 1 < len(hits) else len(block)
        segments.append((marker, _tidy_extracted_text(block[start:end])))
    return segments


def _extract_tp_qualification_commitment_template(tender_raw: str) -> str:
    """提取TP 格式资格审查承诺模板。"""
    match = re.search(
        r"四、资格承诺函(.*?)(?:五、技术偏离及详细配置明细表|五、技术偏离)",
        tender_raw or "",
        re.S,
    )
    if not match:
        return """黑龙江省政府采购供应商资格承诺函

（请优先从招标文件第六章提取原版模板正文；若解析失败，再由人工粘贴原版模板，禁止只保留“请插入模板”的提示语。）

同时附：
1. 营业执照或主体资格证明文件；
2. 法定代表人/单位负责人授权书；
3. 法定代表人/单位负责人及授权代表身份证明；
4. 本项目要求的医疗器械生产/经营许可、备案凭证、注册证；
5. 不得围标串标承诺函等采购文件要求的其他资格材料。""".strip()

    body = re.sub(r"-\s*第\s*\d+\s*页\s*-", "", match.group(1))
    body = "\n".join(_dedupe_consecutive_lines(body.splitlines()))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def _build_tp_quote_summary_table(tender, packages, tender_raw: str) -> str:
    """构建TP 格式报价汇总表。"""
    lines = [
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        "",
        "| 序号(包号) | 货物名称 | 货物报价价格(元) | 货物市场价格(元) | 交货期 |",
        "|---:|---|---:|---:|---|",
    ]

    for pkg in packages:
        lines.append(
            f"| {pkg.package_id} | {pkg.item_name} | 【待填写】 | "
            f"{_extract_tp_package_budget(pkg, tender_raw)} | {_extract_tp_delivery_time(pkg, tender_raw)} |"
        )

    lines.extend([
        "",
        "供应商全称：【待填写：投标人名称】",
        "日期：【待填写：年 月 日】",
    ])
    return "\n".join(lines)


def _build_tp_combined_deviation_detail_table(
    tender,
    pkg,
    tender_raw: str,
    *,
    product=None,
    normalized_result: dict | None = None,
    evidence_result: dict | None = None,
    product_profile: dict | None = None,
) -> str:
    """构建TP 格式combineddeviation明细表格。"""
    requirement_rows, total_requirements = _build_requirement_rows(
        pkg,
        tender_raw,
        product=product,
        normalized_result=normalized_result,
        evidence_result=evidence_result,
        product_profile=product_profile,
    )
    table = _build_deviation_table(
        tender,
        pkg,
        requirement_rows,
        total_requirements,
        product=product,
    )
    table_lines = table.splitlines()
    if table_lines and table_lines[0].startswith("### 四、技术偏离及详细配置明细表"):
        table = "\n".join(table_lines[1:]).lstrip()
    config_table = _build_configuration_table(
        pkg,
        tender_raw,
        product=product,
        product_profile=product_profile,
        normalized_result=normalized_result,
    )

    qty = _extract_tp_package_quantity(pkg, tender_raw)
    delivery_time = _extract_tp_delivery_time(pkg, tender_raw)
    delivery_place = _extract_tp_delivery_place(pkg, tender_raw)
    return "\n".join(
        [
            f"### 合同包{pkg.package_id}：{pkg.item_name}",
            f"数量：{qty}",
            f"交货期：{delivery_time}",
            f"交货地点：{delivery_place}",
            "",
            table,
            "",
            config_table,
        ]
    ).strip()


def _structured_tp_service_points(
    pkg,
    tender_raw: str,
    *,
    product=None,
    normalized_result: dict | None = None,
    evidence_result: dict | None = None,
    product_profile: dict | None = None,
) -> list[str]:
    """从结构化需求中组装 TP 服务要点。"""
    if not normalized_result:
        return []

    rows, _ = _build_requirement_rows(
        pkg,
        tender_raw,
        product=product,
        normalized_result=normalized_result,
        evidence_result=evidence_result,
        product_profile=product_profile,
        category_filter="service_requirement",
    )

    points: list[str] = []
    seen: set[str] = set()

    for row in rows:
        key = _clean_text(row.get("key") or "")
        requirement = _clean_text(row.get("requirement") or "")
        if not key and not requirement:
            continue

        combined = _slice_service_tail(f"{key} {requirement}".strip())
        if not combined:
            continue
        if not _is_tp_service_or_acceptance_clause(combined):
            continue
        if _looks_like_pure_technical_clause(combined):
            continue

        response = _clean_text(row.get("response") or "")
        if response and _has_real_bidder_response(response):
            point = f"{key}：{requirement}；我方响应：{response}".strip("：")
        else:
            point = f"{key}：{requirement}".strip("：")

        if point not in seen:
            seen.add(point)
            points.append(point)

    return points


def _build_tp_service_plan_section(
    packages,
    tender_raw: str,
    *,
    products: dict | None = None,
    normalized_result: dict | None = None,
    evidence_result: dict | None = None,
    product_profiles: dict | None = None,
) -> str:
    """构建TP 格式服务plan章节。"""
    parts: list[str] = []

    for pkg in packages:
        delivery_time = _extract_tp_delivery_time(pkg, tender_raw)
        delivery_place = _extract_tp_delivery_place(pkg, tender_raw)
        raw_service_points = _structured_tp_service_points(
            pkg,
            tender_raw,
            product=(products or {}).get(pkg.package_id),
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profile=(product_profiles or {}).get(pkg.package_id),
        ) or _extract_tp_service_points(pkg, tender_raw)

        parts.extend([
            f"### 合同包{pkg.package_id}：{pkg.item_name}",
            f"交货期：{delivery_time}",
            f"交货地点：{delivery_place}",
            "",
            "#### 1. 供货组织与进度安排",
            f"按照采购文件要求的交货期“{delivery_time}”组织备货、发运和到货交接，落实项目负责人、商务对接人、安装调试工程师和售后服务联系人，确保设备按期送达{delivery_place}。",
            "",
            "#### 2. 包装、运输与到货保护措施",
            "设备按原厂标准包装运输，运输途中做好防震、防潮、防压、防碰撞处理；到货后会同采购人进行外包装检查、数量清点和随机资料核验，如发现异常及时记录并处理。",
            "",
            "#### 3. 卸货、安装与调试措施",
            "供货方负责送货至医院指定地点，并负责安排卸货；设备到场后按院方要求完成定位安装、通电调试、功能测试、性能验证和试运行，形成安装调试记录。",
            "",
            "#### 4. 培训措施",
            "针对科室操作人员和管理人员开展现场培训，培训内容至少包括设备开关机、日常操作、项目运行、故障提示识别、日常维护保养、注意事项等，并提交培训签到和培训记录。",
            "",
            "#### 5. 验收配合措施",
            "按采购文件要求提交医疗器械注册证、出厂检验报告、合格证、装箱单、说明书、配置清单等资料，配合采购人完成到货验收、安装验收、功能验收和参数核验。",
            "",
            "#### 6. 采购文件原始售后要求逐项承诺",
        ])

        if raw_service_points:
            for idx, point in enumerate(raw_service_points, start=1):
                parts.append(f"{idx}）{point}")
        else:
            parts.append("1）按采购文件售后服务要求执行。")

        parts.extend([
            "",
            "#### 7. 备件、维护与升级保障",
            "质保期内按采购文件和投标承诺提供维修、维护、巡检、升级支持；质保期外持续提供有偿维保、备件供应和技术支持，确保设备稳定运行。",
            "",
        ])

    parts.extend([
        "供应商全称：【待填写：投标人名称】",
        "日期：【待填写：年 月 日】",
    ])
    return "\n".join(parts)


def _suggest_tp_qualification_response(item_name: str, requirement: str = "") -> str:
    """生成建议TP 格式资格审查响应。"""
    haystack = _tidy_extracted_text(f"{item_name} {requirement}")
    if any(token in haystack for token in ("中华人民共和国政府采购法》第二十二条", "营业执照", "主体资格")):
        return "四、资格承诺函；营业执照或主体资格证明文件"
    if any(token in haystack for token in ("实施条例第十八条", "资格承诺函")):
        return "四、资格承诺函"
    if any(token in haystack for token in ("法定代表人授权书", "授权书")):
        return "七、法定代表人/单位负责人授权书；八、法定代表人/单位负责人和授权代表身份证明"
    if any(token in haystack for token in ("特定资格", "医疗器械", "注册证", "经营备案", "经营许可", "生产许可")):
        return "四、资格承诺函后附医疗器械生产/经营许可、备案凭证、注册证等特定资格证明材料"
    if any(token in haystack for token in ("围标串标", "承诺")):
        return "四、资格承诺函后附围标串标承诺函（格式自拟）"
    return "四、资格承诺函及后附资格证明材料"


def _suggest_tp_compliance_response(item_name: str, requirement: str = "") -> str:
    """生成建议TP 格式符合性审查响应。"""
    haystack = _tidy_extracted_text(f"{item_name} {requirement}")
    if "投标报价" in haystack:
        return "二、报价书；三、报价一览表"
    if any(token in haystack for token in ("规范性", "符合性", "签署", "盖章", "目录", "格式")):
        return "全册响应文件签字盖章页；二、报价书；七、法定代表人/单位负责人授权书"
    if any(token in haystack for token in ("主要商务条款", "商务条款")):
        return "二、报价书；六、技术服务和售后服务的内容及措施"
    if "联合体投标" in haystack:
        return "二、报价书或联合体协议（如适用）"
    if any(token in haystack for token in ("技术部分实质性内容", "品牌", "明确响应", "实质性要求")):
        return "五、技术偏离及详细配置明细表；六、技术服务和售后服务的内容及措施"
    if "其他要求" in haystack:
        return "十一、投标人关联单位的说明；附四、投标无效情形汇总及自检表；围标串标承诺函"
    return "对应章节及后附证明材料"


def _suggest_tp_detailed_response_location(item_name: str, requirement: str = "") -> str:
    """生成建议TP 格式详细响应location。"""
    haystack = _tidy_extracted_text(f"{item_name} {requirement}")
    if any(token in haystack for token in ("技术参数", "配置", "装箱", "核心性能", "品牌", "型号", "规格")):
        return "五、技术偏离及详细配置明细表"
    if any(token in haystack for token in ("供货组织", "进度安排", "供货进度", "交货期")):
        return "六、技术服务和售后服务的内容及措施 / 1.供货组织与进度安排"
    if any(token in haystack for token in ("包装", "运输", "到货保护", "签收", "验货")):
        return "六、技术服务和售后服务的内容及措施 / 2.包装、运输与到货保护措施"
    if any(token in haystack for token in ("安装", "调试", "验收", "卸货")):
        return "六、技术服务和售后服务的内容及措施 / 3.卸货、安装与调试措施 / 5.验收配合措施"
    if "培训" in haystack:
        return "六、技术服务和售后服务的内容及措施 / 4.培训措施"
    if any(token in haystack for token in ("售后", "维保", "备件", "升级", "响应速度")):
        return "六、技术服务和售后服务的内容及措施 / 6.采购文件原始售后要求逐项承诺 / 7.备件、维护与升级保障"
    if any(token in haystack for token in ("报价", "预算", "价格")):
        return "二、报价书；三、报价一览表"
    return "【待填写：对应章节/材料】"


def _suggest_tp_detailed_response_note(item_name: str, requirement: str = "") -> str:
    """生成建议TP 格式详细响应备注。"""
    haystack = _tidy_extracted_text(f"{item_name} {requirement}")
    if any(token in haystack for token in ("技术参数", "配置", "装箱", "核心性能")):
        return "逐条填写品牌、型号、规格、配置、响应值及偏离情况，不得仅写“响应/完全响应”。"
    if any(token in haystack for token in ("供货组织", "进度安排", "供货进度", "交货期")):
        return "围绕备货、发运、到货、签收、节点控制和责任人安排逐项说明。"
    if any(token in haystack for token in ("包装", "运输", "到货保护", "签收", "验货")):
        return "围绕包装标准、运输方式、风险预防、异常处理和到货交接逐项说明。"
    if any(token in haystack for token in ("安装", "调试", "验收", "卸货")):
        return "围绕卸货安装、通电调试、功能验证、验收资料和验收配合逐项说明。"
    if "培训" in haystack:
        return "围绕培训对象、培训内容、培训方式、培训记录和持续支持逐项说明。"
    if any(token in haystack for token in ("售后", "维保", "备件", "升级", "响应速度")):
        return "逐条对应采购文件售后服务要求，明确响应时效、维保周期、备件保障和升级支持。"
    if any(token in haystack for token in ("报价", "预算", "价格")):
        return "核对报价唯一性、未超预算、分项与总价一致，并补充必要测算说明。"
    return "【待填写：如何满足该评审项】"


def _extract_tp_qualification_rows(pkg, tender_raw: str) -> list[tuple[str, str]]:
    """提取TP 格式资格审查行。"""
    block = _extract_tp_review_block(
        tender_raw,
        anchor_patterns=[r"表一资格性审查表[:：]?\s*表一资格性审查表[:：]?", r"表一资格性审查表[:：]?"],
        stop_patterns=[r"表二符合性审查表[:：]?", r"第五章\s*主要合同条款", r"第六章\s*响应文件格式"],
    )
    pkg_block = _extract_tp_contract_package_block(block, str(getattr(pkg, "package_id", "") or ""))
    if not pkg_block:
        return [
            ("符合《中华人民共和国政府采购法》第二十二条规定的条件。", "提交有效营业执照（或事业法人登记证或身份证等相关证明）副本复印件，或按黑龙江省资格承诺函路径提交承诺并附证明材料。"),
            ("承诺不存在《中华人民共和国政府采购法实施条例》第十八条规定情形。", "提供《黑龙江省政府采购供应商资格承诺函》承诺并加盖公章。"),
            ("未被列入失信被执行人、重大税收违法案件当事人名单、政府采购严重违法失信行为记录名单。", "提供资格承诺函承诺并接受查询。"),
            ("法定代表人授权书", "提供标准格式授权书并按要求签字、加盖公章。"),
            ("特定资格要求", "按产品管理类别提交医疗器械生产许可证/经营备案凭证/经营许可证/注册证；如不按医疗器械管理则无需提供。"),
            ("围标串标承诺", "提供承诺函，格式自拟。"),
        ]

    marker_pat = re.compile(r"(（[一二三四五六七八九十]+）|法定代表人授权书(?=\s*提供)|特定资格要求|围标串标承诺)")
    hits = list(marker_pat.finditer(pkg_block))
    rows: list[tuple[str, str]] = []
    for idx, match in enumerate(hits):
        start = match.start()
        end = hits[idx + 1].start() if idx + 1 < len(hits) else len(pkg_block)
        segment = _tidy_extracted_text(pkg_block[start:end])
        label = _tidy_extracted_text(match.group(1))
        if not segment:
            continue

        if label in {"法定代表人授权书", "特定资格要求", "围标串标承诺"}:
            requirement = segment[len(label):].strip(" ：:") or segment
            rows.append((label, requirement))
            continue

        split_points = [
            segment.find(token)
            for token in (
                "在中华人民共和国注册",
                "供应商按照",
                "供应商提供",
                "提供相关承诺函",
                "拟参加本项目供应商",
            )
            if segment.find(token) > 0
        ]
        split_at = min(split_points) if split_points else -1
        if split_at > 0:
            rows.append((segment[:split_at].strip(), segment[split_at:].strip()))
            continue

        split_at = segment.find("。")
        rows.append((segment[: split_at + 1].strip(), segment[split_at + 1 :].strip()) if split_at > 0 else (label, segment))

    return rows


def _build_tp_qualification_review_section(tender, packages, tender_raw: str) -> str:
    """构建TP 格式资格审查章节。"""
    headers = _select_tp_headers(
        tender,
        "qualification_review_table",
        ["序号", "审查项", "招标文件要求", "响应文件对应内容", "是否满足", "备注"],
    )
    parts: list[str] = []

    for pkg in packages:
        rows = [
            _render_tp_row(
                headers,
                seq=str(idx),
                item_name=item_name,
                requirement=requirement,
                response_placeholder=_suggest_tp_qualification_response(item_name, requirement),
            )
            for idx, (item_name, requirement) in enumerate(_extract_tp_qualification_rows(pkg, tender_raw), start=1)
        ]
        parts.extend([
            f"### 合同包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()


def _extract_tp_compliance_rows(pkg, tender_raw: str) -> list[tuple[str, str]]:
    """提取TP 格式符合性审查行。"""
    block = _extract_tp_review_block(
        tender_raw,
        anchor_patterns=[r"表二符合性审查表[:：]?\s*表二符合性审查表[:：]?", r"表二符合性审查表[:：]?"],
        stop_patterns=[
            r"采购人、采购代理机构应当视为投标无效处理[：:]?",
            r"响应文件存在下列任意一条的，则响应文件无效[：:]?",
            r"第五章\s*主要合同条款",
            r"第六章\s*响应文件格式",
        ],
    )
    pkg_block = _extract_tp_contract_package_block(block, str(getattr(pkg, "package_id", "") or ""))
    if not pkg_block:
        return [
            ("投标报价", "投标报价（包括分项报价，投标总报价）只能有一个有效报价且不超过采购预算或最高限价，投标报价不得缺项、漏项。"),
            ("投标文件规范性、符合性", "投标文件的签署、盖章、涂改、删除、插字、公章使用、格式、文字、目录等符合招标文件要求或对投标无实质性影响。"),
            ("主要商务条款", "审查投标人出具的“满足主要商务条款的承诺书”，且有法定代表人或授权代表签字并加盖单位公章。"),
            ("联合体投标", "符合关于联合体投标的相关规定。"),
            ("技术部分实质性内容", "明确所投产品品牌/型号/服务内容，并对招标文件提出的要求和条件作出明确响应，满足全部实质性要求。"),
            ("其他要求", "不存在围标、串标、法律法规规定的其他无效投标情形及作者属性异常一致等情形。"),
        ]

    rows = []
    for label, segment in _extract_tp_named_segments(pkg_block, _TP_COMPLIANCE_LABELS):
        requirement = segment[len(label):].strip(" ：:") or segment
        rows.append((label, requirement))
    return rows


def _build_tp_compliance_review_section(tender, packages, tender_raw: str) -> str:
    """构建TP 格式符合性审查章节。"""
    headers = _select_tp_headers(
        tender,
        "compliance_review_table",
        ["序号", "审查项", "招标文件要求", "响应文件对应内容", "是否满足", "备注"],
    )
    parts: list[str] = []

    for pkg in packages:
        rows = [
            _render_tp_row(
                headers,
                seq=str(idx),
                item_name=item_name,
                requirement=requirement,
                response_placeholder=_suggest_tp_compliance_response(item_name, requirement),
            )
            for idx, (item_name, requirement) in enumerate(_extract_tp_compliance_rows(pkg, tender_raw), start=1)
        ]
        parts.extend([
            f"### 合同包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()


def _build_tp_detailed_review_rows(pkg, tender_raw: str) -> list[tuple[str, str]]:
    """构建TP 格式详细评审行。"""
    delivery_time = _extract_tp_delivery_time(pkg, tender_raw)
    delivery_place = _extract_tp_delivery_place(pkg, tender_raw)
    service_points = _extract_tp_service_points(pkg, tender_raw)
    service_digest = "；".join(service_points[:3]) if service_points else "按采购文件售后服务要求逐条承诺。"

    return [
        (
            "技术参数逐条响应",
            "根据采购文件技术参数逐条填写品牌、型号、规格、配置、响应值及偏离情况；★/※条款必须实质性响应。",
        ),
        (
            "详细配置与装箱清单",
            "根据采购文件装箱配置单、随机附件和随机资料要求逐项列明标配、选配、配件数量及说明。",
        ),
        (
            "供货组织与进度安排",
            f"按照采购文件要求在“{delivery_time}”内完成供货，交货地点为“{delivery_place}”，并逐项说明备货、发运、到货、签收节点安排。",
        ),
        (
            "包装运输与到货保护",
            "结合设备特性说明原厂包装、防震防潮、防损措施、运输方案、运输风险预防和到货交接验货安排。",
        ),
        (
            "安装调试、培训与验收配合",
            "说明卸货安装、通电调试、功能验证、操作培训、验收资料准备和到货/安装/功能验收配合措施。",
        ),
        (
            "售后服务与维保承诺",
            f"逐条响应采购文件售后要求并形成承诺清单：{service_digest}",
        ),
        (
            "报价完整性与合规性",
            "核对报价唯一性、分项与总价一致性、是否超预算以及与技术响应、供货方案之间的一致性。",
        ),
    ]


def _build_tp_detailed_review_section(tender, packages, tender_raw: str) -> str:
    """构建TP 格式详细评审章节。"""
    headers = _select_tp_headers(
        tender,
        "detailed_review_table",
        ["序号", "评审项", "采购文件评分要求", "响应文件对应内容", "自评说明", "证明材料/页码"],
    )
    parts: list[str] = []

    for pkg in packages:
        rows = [
            _render_tp_row(
                headers,
                seq=str(idx),
                item_name=item_name,
                requirement=requirement,
                response_placeholder=_suggest_tp_detailed_response_location(item_name, requirement),
                status_placeholder=_suggest_tp_detailed_response_note(item_name, requirement),
                evidence_placeholder="【待填写：页码】",
            )
            for idx, (item_name, requirement) in enumerate(_build_tp_detailed_review_rows(pkg, tender_raw), start=1)
        ]
        parts.extend([
            f"### 合同包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()


def _extract_tp_invalid_items(tender_raw: str) -> list[str]:
    """提取TP 格式无效项。"""
    text = _normalize_dense_text(tender_raw)
    if not text:
        return []

    blocks: list[str] = []
    patterns = [
        (
            r"采购人、采购代理机构应当视为投标无效处理[：:]?",
            [r"7[．.]\s*供应商必须保证", r"24\s*电子响应文件签字"],
        ),
        (
            r"响应文件存在下列任意一条的，则响应文件无效[：:]?",
            [r"6\.\s*供应商出现下列情况之一的，响应文件无效[:：]?", r"供应商出现下列情况之一的，响应文件无效[:：]?"],
        ),
        (
            r"供应商出现下列情况之一的，响应文件无效[：:]?",
            [r"7\.\s*供应商禁止行为", r"供应商禁止行为"],
        ),
    ]

    for anchor, stops in patterns:
        block = _extract_tp_review_block(text, [anchor], stops)
        if block:
            blocks.append(block)

    items: list[str] = []
    enum_pat = re.compile(
        r"(?:（\s*[一二三四五六七八九十]+\s*）|[(（]?\s*\d+\s*[)）])\s*(.*?)(?=(?:（\s*[一二三四五六七八九十]+\s*）|[(（]?\s*\d+\s*[)）])|$)"
    )
    for block in blocks:
        for match in enum_pat.finditer(block):
            item = _tidy_extracted_text(match.group(1)).rstrip("；;。")
            if item:
                items.append(f"{item}。")

    extra_patterns = [
        r"资格性审查和符合性审查中凡有其中任意一项未通过.*?按无效投标处理。",
        r"若出现供应商因在投标客户端中对应答点标记错误.*?由投标人自行承担责任。",
    ]
    for pat in extra_patterns:
        for match in re.finditer(pat, text):
            items.append(_tidy_extracted_text(match.group(0)).rstrip("；;。") + "。")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items:
        compact = _normalize_tp_procurement_terms(item)
        compact = re.split(r"\s*7\s*[．.]\s*供应商必须保证", compact, maxsplit=1)[0].strip()
        if not compact or compact in seen:
            continue
        if any(token in compact for token in ("主要商务要求", "技术标准与要求", "附表", "参数性质", "第 7 页", "第 8 页")):
            continue
        seen.add(compact)
        cleaned.append(compact)
    return cleaned


def _build_tp_invalid_bid_checklist(tender, tender_raw: str) -> str:
    """构建TP 格式无效投标checklist。"""
    headers = _select_tp_headers(tender, "invalid_bid_table", ["序号", "无效情形", "自检结果", "备注"])
    items = _extract_tp_invalid_items(tender_raw) or [
        "投标人未按招标文件要求参加远程开标会的。",
        "投标人未在规定时间内完成电子投标文件在线解密的。",
        "经检查数字证书无效的投标文件。",
        "投标人自身原因造成电子投标文件未能解密的。",
        "任意一条不满足谈判文件★号条款要求的。",
        "单项产品五条及以上不满足非★号条款要求的。",
        "供应商所提报的技术参数没有如实填写，没有与“竞争性谈判文件技术要求”一一对应，只简单填写“响应或完全响应”的以及未逐条填写应答的。",
        "供应商提报的技术参数中没有明确品牌、型号、规格、配置等。",
        "单项商品报价超单项预算的。",
        "响应产品中如要求安装软件，应提供正版软件，否则响应无效。",
        "政府采购执行节能产品政府强制采购和优先采购政策，如采购人所采购产品为政府强制采购的节能产品，供应商所投产品的品牌及型号必须为清单中有效期内产品并提供证明文件，否则其响应将作为无效响应被拒绝。",
        "非★条款有重大偏离经谈判小组专家认定无法满足竞争性谈判文件需求的。",
        "未按竞争性谈判文件规定要求签字、盖章的。",
        "响应文件中提供虚假材料的。",
        "提交的技术参数与所提供的技术证明文件不一致的。",
        "所报项目在实际运行中，其使用成本过高、使用条件苛刻的需经谈判小组确定后不能被采购人接受的。",
        "法定代表人/单位负责人授权书无法定代表人/单位负责人签字或没有加盖公章的。",
        "参加政府采购活动前三年内，在经营活动中有重大违法记录的。",
        "供应商对采购人、代理机构、谈判小组及其工作人员施加影响，有碍公平、公正的。",
        "单位负责人为同一人或者存在直接控股、管理关系的不同供应商参与本项目同一合同项下的投标的，其相关投标将被认定为投标无效。",
        "属于串通投标，或者依法被视为串通投标的。",
        "按有关法律、法规、规章规定属于响应无效的。",
        "谈判小组在谈判过程中，应以供应商提供的响应文件为谈判依据，不得接受响应文件以外的任何形式的文件资料。",
        "资格性审查和符合性审查中凡有其中任意一项未通过的，按无效投标处理。",
        "在投标客户端中对应答点标记错误，导致评审专家无法正常查阅而否决投标。",
    ]

    rows = [
        _render_tp_row(headers, seq=str(idx), invalid_item=item, self_check_placeholder="【待填写：符合/不符合】")
        for idx, item in enumerate(items, start=1)
    ]
    return _md_table(headers, rows)


def _extract_tp_summary_rows(tender_raw: str) -> list[dict]:
    """提取TP 格式汇总行。"""
    rows = []
    pattern = re.compile(
        r"(?P<pkg>\d+)\s+(?P<name>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+详见采购文件\s+(?P<budget>[0-9,]+(?:\.\d+)?)",
        re.S,
    )
    for match in pattern.finditer(tender_raw or ""):
        rows.append(
            {
                "package_id": match.group("pkg").strip(),
                "item_name": " ".join(match.group("name").split()),
                "quantity": match.group("qty").strip(),
                "budget": match.group("budget").replace(",", "").strip(),
            }
        )
    return rows


def _find_tp_summary_row(tender_raw: str, package_id: str) -> dict | None:
    """查找TP 格式汇总行。"""
    for row in _extract_tp_summary_rows(tender_raw):
        if row["package_id"] == str(package_id):
            return row
    return None


def _extract_tp_package_quantity(pkg, tender_raw: str) -> str:
    """提取TP 格式包件数量。"""
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    match = re.search(r"数量\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*台", block)
    if match:
        return match.group(1).strip()

    row = _find_tp_summary_row(tender_raw, pkg.package_id)
    if row and row.get("quantity"):
        return row["quantity"]

    quantity = getattr(pkg, "quantity", None)
    if quantity not in (None, ""):
        return str(quantity).strip()
    return "【待填写：数量】"


def _extract_tp_package_budget(pkg, tender_raw: str) -> str:
    """提取TP 格式包件预算。"""
    row = _find_tp_summary_row(tender_raw, pkg.package_id)
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


def _extract_tp_delivery_time(pkg, tender_raw: str) -> str:
    """提取TP 格式交付时间。"""
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    for raw_line in block.splitlines():
        line = _tidy_extracted_text(raw_line)
        if "标的提供的时间" in line:
            value = line.split("标的提供的时间", 1)[1].strip(" ：:")
            if value:
                return _truncate_commercial_tail(value)
        if "合同履行期限" in line:
            value = line.split("合同履行期限", 1)[1].strip(" ：:")
            if value:
                return _truncate_commercial_tail(value)

    text = _normalize_dense_text(tender_raw)
    match = re.search(
        rf"合同包\s*{re.escape(str(pkg.package_id))}\s*[（(].*?[）)]\s*[：:]?\s*(签订合同后\s*\d+\s*个工作日送达指定地点)",
        text,
    )
    if match:
        return _truncate_commercial_tail(_tidy_extracted_text(match.group(1)))
    return "按采购文件要求"


def _extract_tp_delivery_place(pkg, tender_raw: str) -> str:
    """提取TP 格式交付地点。"""
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    for raw_line in block.splitlines():
        line = _tidy_extracted_text(raw_line)
        if "标的提供的地点" in line:
            value = line.split("标的提供的地点", 1)[1].strip(" ：:")
            if value:
                return _truncate_commercial_tail(value, keep_delivery_place=True)
    return "采购人指定地点"


def _extract_tp_requirements_chapter(tender_raw: str) -> str:
    """提取TP 格式需求chapter。"""
    match = re.search(
        r"第二章\s*采购人需求(.*?)(?=第三章\s*供应商须知|第三章|第[三四五六七八九十]+章|$)",
        tender_raw or "",
        re.S,
    )
    return (match.group(1) if match else tender_raw) or ""


def _find_tp_package_block(tender_raw: str, package_id: str) -> str:
    """查找TP 格式包件文本块。"""
    scope = _extract_tp_requirements_chapter(tender_raw)
    all_pkg = list(re.finditer(r"合同包\s*\d+\s*[（(]", scope))
    start_pat = re.compile(rf"合同包\s*{re.escape(str(package_id))}\s*[（(]")

    start = None
    end = len(scope)
    for idx, match in enumerate(all_pkg):
        if start_pat.match(match.group(0)):
            start = match.start()
            if idx + 1 < len(all_pkg):
                end = all_pkg[idx + 1].start()
            break

    if start is None:
        return scope
    return scope[start:end]


def _extract_tp_detail_rows(block: str, pkg) -> list[dict]:
    """提取TP 格式明细行。"""
    _ = pkg
    rows = []
    match = re.search(r"四、装箱配置单[：:]?(.*?)(?:五、质保|六、售后服务要求|七、)", block, re.S)
    if not match:
        return rows

    lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    for line in lines:
        item = _tidy_extracted_text(line)
        matched = re.match(r"^\d+\s+(.+?)\s+(\d+)\s*$", item)
        if matched:
            rows.append({"name": matched.group(1).strip(), "qty": matched.group(2).strip(), "remark": ""})
    return rows

def _looks_like_pure_technical_clause(text: str) -> bool:
    """判断likepure技术条款。"""
    normalized = _clean_text(text)
    if not normalized:
        return False

    tech_keywords = (
        "设备名称", "检测原理", "检测方法", "激光器", "通道", "FITC", "PE", "APC",
        "CV", "样本位", "试剂位", "分析速度", "检测速度", "光源输出",
        "温度范围", "电压范围", "功率", "灵敏度", "分辨率", "精密度",
    )

    if any(k in normalized for k in tech_keywords):
        return True

    if re.search(r"\d", normalized) and any(k in normalized for k in ("℃", "V", "W", "%", "nm", "μL", "mL", "min", "秒")):
        return True

    return False


def _extract_tp_requirement_rows(block: str, pkg) -> list[dict]:
    """提取TP 格式需求行。"""
    _ = pkg
    rows = []
    match = re.search(r"三、技术参数[：:]?(.*?)(?:四、装箱配置单|五、质保|六、售后服务要求)", block, re.S)
    if not match:
        return rows

    raw_lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    merged: list[str] = []
    for line in raw_lines:
        if re.match(r"^(?:[※★]?\d+[、.]|[※★]?\d+\s*[、.])", line):
            merged.append(line)
        elif merged:
            merged[-1] += " " + line

    for item in merged:
        rows.append({"requirement": _tidy_extracted_text(item).replace("|", "/")})
    return rows


def _extract_tp_service_points(pkg, tender_raw: str) -> list[str]:
    """提取TP 格式服务要点。"""
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    if not block:
        return []

    lines = [_clean_text(line) for line in block.splitlines() if _clean_text(line)]
    collecting = False
    collected: list[str] = []
    for line in lines:
        compact = line.strip()
        if not collecting and "售后服务要求" in compact:
            collecting = True
            continue
        if not collecting:
            continue
        if any(token in compact for token in ("说明", "合同包", "第六章", "第七章", "第八章")):
            break
        collected.append(compact)

    if not collected:
        return []

    joined = " ".join(collected)
    points = []
    enum_pat = re.compile(
        r"(?:^|(?<=\s))(?:\d+|[一二三四五六七八九十]+)\s*[、.]\s*(.*?)(?=(?:\s+(?:\d+|[一二三四五六七八九十]+)\s*[、.])|$)"
    )
    for item in enum_pat.findall(joined):
        value = _tidy_extracted_text(item)
        value = re.sub(r"(\d)\s+(小时|分钟|次|天|年|个|台|项|页)", r"\1\2", value)
        value = re.sub(r"\s*/\s*", "/", value)
        value = re.sub(r"([\u4e00-\u9fff])\s+([A-Z]{2,})", r"\1\2", value)
        value = re.sub(r"([A-Z]{2,})\s+([\u4e00-\u9fff])", r"\1\2", value)
        value = re.sub(r"[；;]{2,}", "；", value)
        value = _normalize_tp_procurement_terms(value)
        if value:
            points.append(value)
    return points


def _build_tp_sections(
    tender,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list | None = None,
    *,
    normalized_result: dict | None = None,
    evidence_result: dict | None = None,
    product_profiles: dict | None = None,
) -> list:
    """构建TP 格式章节。"""
    packages = active_packages or tender.packages
    sections = []

    sections.append(
        BidDocumentSection(
            section_title="一、响应文件封面格式",
            content=f"""
政 府 采 购
响 应 文 件

项目名称：{tender.project_name}
项目编号：{tender.project_number}

供应商全称：（公章）【待填写：投标人名称】
授权代表：【待填写：授权代表】
电话：【待填写：联系电话】
谈判日期：【待填写：日期】
""".strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="二、报价书",
            content=f"""
（供应商全称）授权（授权代表姓名）【待填写】（职务、职称）【待填写】为响应供应商代表，
参加贵方组织的（项目编号：{tender.project_number}，项目名称：{tender.project_name}）谈判的有关活动，并对本项目进行报价。为此：

1. 提供供应商须知规定的全部响应文件；
2. 报价的总价为（大写）【待填写】元人民币；
3. 保证遵守竞争性谈判文件中的有关规定；
4. 保证忠实地执行买卖双方所签的《政府采购合同》，并承担《合同》约定的责任义务；
5. 愿意向贵方提供任何与该项活动有关的数据、情况和技术资料；
6. 与本活动有关的一切往来通讯请寄：
   地址：【待填写】  邮编：【待填写】
   电话：【待填写】  传真：【待填写】

供应商全称：【待填写：投标人名称】
日期：【待填写：年 月 日】
""".strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="三、报价一览表",
            content=_build_tp_quote_summary_table(tender, packages, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="四、资格承诺函",
            content=_extract_tp_qualification_commitment_template(tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="五、技术偏离及详细配置明细表",
            content="\n\n".join(
                _build_tp_combined_deviation_detail_table(
                    tender,
                    pkg,
                    tender_raw,
                    product=(products or {}).get(pkg.package_id),
                    normalized_result=normalized_result,
                    evidence_result=evidence_result,
                    product_profile=(product_profiles or {}).get(pkg.package_id),
                )
                for pkg in packages
            ).strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="六、技术服务和售后服务的内容及措施",
            content=_build_tp_service_plan_section(
                packages,
                tender_raw,
                products=products,
                normalized_result=normalized_result,
                evidence_result=evidence_result,
                product_profiles=product_profiles,
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="七、法定代表人/单位负责人授权书",
            content=_build_tp_template_section(
                tender_raw,
                "七、法定代表人/单位负责人授权书",
                f"""
（报价单位全称）法定代表人/单位负责人【待填写：法定代表人姓名】授权【待填写：授权代表姓名】为响应供应商代表，
参加贵处组织的 {tender.project_name}（项目编号：{tender.project_number}）竞争性谈判，全权处理本活动中的一切事宜。

法定代表人/单位负责人签字：【待填写】
供应商全称（公章）：【待填写：投标人名称】
日期：【待填写：年 月 日】
附：
授权代表姓名：【待填写】
授权代表（签字）：【待填写】
职务：【待填写】
详细通讯地址：【待填写】
邮政编码：【待填写】
传真：【待填写】
电话：【待填写】
""".strip(),
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="八、法定代表人/单位负责人和授权代表身份证明",
            content=_build_tp_template_section(
                tender_raw,
                "八、法定代表人/单位负责人和授权代表身份证明",
                """
（法定代表人/单位负责人身份证正反面复印件）
（授权代表身份证正反面复印件）
供应商全称：【待填写：投标人名称】
""".strip(),
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="九、小微企业声明函",
            content=_build_tp_template_section(
                tender_raw,
                "九、小微企业声明函",
                "按招标文件原格式保留《中小企业声明函》；不适用时注明“本项不适用”。",
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="十、残疾人福利性单位声明函",
            content=_build_tp_template_section(
                tender_raw,
                "十、残疾人福利性单位声明函",
                "按招标文件原格式保留《残疾人福利性单位声明函》；不适用时注明“本项不适用”。",
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="十一、投标人关联单位的说明",
            content=_build_tp_template_section(
                tender_raw,
                "十一、投标人关联单位的说明",
                """
说明：投标人应当如实披露与本单位存在下列关联关系的单位名称：
（1）与投标人单位负责人为同一人的其他单位；
（2）与投标人存在直接控股、管理关系的其他单位。
""".strip(),
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title=_TP_APPENDIX_TITLES[0],
            content=_build_tp_qualification_review_section(tender, packages, tender_raw),
        )
    )
    sections.append(
        BidDocumentSection(
            section_title=_TP_APPENDIX_TITLES[1],
            content=_build_tp_compliance_review_section(tender, packages, tender_raw),
        )
    )
    sections.append(
        BidDocumentSection(
            section_title=_TP_APPENDIX_TITLES[2],
            content=_build_tp_detailed_review_section(tender, packages, tender_raw),
        )
    )
    sections.append(
        BidDocumentSection(
            section_title=_TP_APPENDIX_TITLES[3],
            content=_build_tp_invalid_bid_checklist(tender, tender_raw),
        )
    )

    return sections
