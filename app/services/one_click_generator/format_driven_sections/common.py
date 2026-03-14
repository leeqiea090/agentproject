"""格式驱动章节生成的公共工具与共享提取逻辑。"""
from __future__ import annotations

import re

from app.schemas import BidDocumentSection, TenderDocument, ProcurementPackage

__all__ = [
    "re",
    "BidDocumentSection",
    "TenderDocument",
    "ProcurementPackage",
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

def _extract_review_block(tender_raw: str, title_keywords: list[str], stop_keywords: list[str] | None = None) -> str:
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
    return " ".join(str(value or "").replace("|", "/").split())


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
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
    cells = {str(k): _clean_text(v) for k, v in (getattr(row, "cells", {}) or {}).items()}
    source_text = _clean_text(getattr(row, "source_text", ""))
    package_id = _clean_text(getattr(row, "package_id", ""))
    if source_text:
        cells["_source_text"] = source_text
    if package_id:
        cells["_package_id"] = package_id
    return cells


def _pick_template_rows(table, pkg=None) -> list[dict[str, str]]:
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
    key = _clean_text(key)
    key = key.replace("商务部分 ", "")
    key = key.replace("投标报价 ", "")
    return key


def _is_valid_invalid_item(text: str) -> bool:
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


def _extract_package_summary_rows(tender_raw: str) -> list[dict]:
    """
    从首页/采购邀请中的“磋商内容”表抽包号、名称、数量、预算、交货期、地点。
    当前项目页里每一包类似：
    1 X射线血液辐照设备 1 975,000.00 合同签订后90个日历日内交货 甲方指定地点
    """
    rows: list[dict] = []
    pattern = re.compile(
        r"(?P<pkg>\d+)\s+"
        r"(?P<name>.+?)\s+"
        r"(?P<qty>\d+(?:\.\d+)?)\s+"
        r"(?P<budget>[0-9,]+(?:\.\d+)?)\s+"
        r"(?P<delivery>合同签订后[^\n]*?交货)\s+"
        r"(?P<place>甲方指定地点|采购人指定地点|[^\n]+?地点)",
        re.S,
    )
    for m in pattern.finditer(tender_raw):
        rows.append(
            {
                "package_id": m.group("pkg").strip(),
                "item_name": " ".join(m.group("name").split()),
                "quantity": m.group("qty").strip(),
                "budget": m.group("budget").replace(",", "").strip(),
                "delivery_time": " ".join(m.group("delivery").split()),
                "delivery_place": " ".join(m.group("place").split()),
            }
        )
    return rows


def _find_summary_row(tender_raw: str, package_id: str) -> dict | None:
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


def _extract_delivery_time(pkg, tender_raw: str) -> str:
    row = _find_summary_row(tender_raw, pkg.package_id)
    if row and row.get("delivery_time"):
        return row["delivery_time"]

    block = _find_package_block(tender_raw, pkg.package_id)
    if block:
        patterns = [
            r"标的提供的时间\s*([^\n]+)",
            r"合同履行期限\s*([^\n]+)",
            r"交货期[：:]\s*([^\n]+)",
        ]
        for pat in patterns:
            m = re.search(pat, block)
            if m:
                return " ".join(m.group(1).split())

    return "按采购文件要求"


def _extract_delivery_place(pkg, tender_raw: str) -> str:
    row = _find_summary_row(tender_raw, pkg.package_id)
    if row and row.get("delivery_place"):
        return row["delivery_place"]

    block = _find_package_block(tender_raw, pkg.package_id)
    if block:
        patterns = [
            r"标的提供的地点\s*([^\n]+)",
            r"交货地点[：:]\s*([^\n]+)",
            r"供货地点[：:]\s*([^\n]+)",
        ]
        for pat in patterns:
            m = re.search(pat, block)
            if m:
                return " ".join(m.group(1).split())

    return "甲方指定地点"


def _extract_package_budget(pkg, tender_raw: str) -> str:
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
    for name in ("budget_amount", "budget", "package_budget", "estimated_amount", "amount"):
        value = getattr(pkg, name, None)
        if value not in (None, ""):
            try:
                return f"{float(value):,.2f}"
            except Exception:
                return str(value)
    return "【待填写：预算金额】"


def _extract_requirements_chapter(tender_raw: str) -> str:
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
    block = _find_package_block(tender_raw, pkg.package_id)
    if block:
        m = re.search(r"二、数量[：:]\s*([0-9]+(?:\.[0-9]+)?)", block)
        if m:
            return _normalize_number_text(m.group(1))
    return _normalize_number_text(getattr(pkg, "quantity", "")) or "【待填写：数量】"


def _merge_numbered_lines(text: str) -> list[str]:
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
    block = _find_package_block(tender_raw, pkg.package_id)
    if not block:
        return ["详见采购文件技术要求"]

    m = re.search(
        r"(设备名称：.*?)(?:四、装箱配置单：|四、装箱配置单|五、质保：)",
        block,
        re.S,
    )
    if not m:
        return ["详见采购文件技术要求"]

    points = _merge_numbered_lines(m.group(1))
    clean_points: list[str] = []
    for p in points:
        if "说明 打“★”号条款" in p:
            continue
        if p.strip():
            clean_points.append(p.strip())
    return clean_points or ["详见采购文件技术要求"]


def _extract_service_points(pkg: ProcurementPackage, tender_raw: str) -> list[str]:
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
    blocks = []
    for anchors in ([r"投标无效[条款情形]*"], [r"响应无效[条款情形]*"], [r"其他投标无效条款"], [r"其他响应无效条款"]):
        block = _extract_anchor_block(
            tender_raw,
            anchor_patterns=anchors,
            stop_patterns=[r"评分标准", r"详细评审", r"响应文件格式", r"第[一二三四五六七八九十]+章"],
            max_chars=6000,
        )
        if block:
            blocks.append(block)
    items: list[str] = []
    for block in blocks:
        for item in _merge_bullet_lines(block):
            if any(tok in item for tok in ("审查表", "招标文件要求", "响应文件对应内容")):
                continue
            if len(item) >= 6 and item not in items:
                items.append(item.rstrip("；;。") + "。")
    return items


def _extract_scoring_items(tender, tender_raw: str) -> list[str]:
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
