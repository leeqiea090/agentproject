from __future__ import annotations

from app.schemas import BidDocumentSection, TenderDocument, ProcurementPackage


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

def _build_cs_qualification_review_section(tender, packages, tender_raw: str) -> str:
    headers = ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"]
    tpl = getattr(tender, "qualification_review_table", None)

    fallback_rows = [
        {"review_item": "符合《中华人民共和国政府采购法》第二十二条规定的条件", "tender_requirement": "提供黑龙江省政府采购供应商资格承诺函或等效证明材料"},
        {"review_item": "不存在《中华人民共和国政府采购法实施条例》第十八条情形", "tender_requirement": "提供承诺函或等效证明材料"},
        {"review_item": "未被列入失信被执行人、重大税收违法失信主体、政府采购严重违法失信行为记录名单", "tender_requirement": "提供承诺函或查询结果"},
        {"review_item": "法定代表人/单位负责人授权书及身份证明材料齐全", "tender_requirement": "按招标文件标准格式提交授权书、法定代表人/授权代表身份证明"},
        {"review_item": "本项目特定资格要求", "tender_requirement": "按医疗器械目录分类提供经营许可/备案凭证/生产许可证/注册证；非医疗器械无需提供"},
    ]

    parts: list[str] = []
    for pkg in packages:
        picked = _pick_template_rows(tpl, pkg) if tpl else []
        source_rows = picked or fallback_rows

        rows: list[list[str]] = []
        for idx, item in enumerate(source_rows, start=1):
            review_item = item.get("review_item") or f"审查项{idx}"
            tender_requirement = item.get("tender_requirement") or item.get("_source_text") or review_item
            rows.append([
                str(idx),
                review_item,
                tender_requirement,
                "【待填写：对应材料名称/页码】",
                "【待填写：满足/不满足】",
                "【待填写】",
            ])

        parts.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()


def _build_cs_compliance_review_section(tender_raw: str) -> str:
    fallback_rows = [
        "响应文件签署、盖章、密封、格式、页码、装订等符合采购文件要求。",
        "报价唯一且未超过采购预算或最高限价，不存在缺项漏项。",
        "对采购文件实质性条款、★/※条款逐条作出明确响应。",
        "不存在采购文件载明的其他无效响应情形。",
    ]

    block = _extract_review_block(
        tender_raw,
        ["符合性审查表", "符合性审查", "符合性审查要求"],
        ["详细评审", "评分标准", "响应文件格式", "合同包", "采购包"],
    )
    rows = _extract_review_rows_from_block(block) or fallback_rows

    lines = [
        "| 序号 | 审查项 | 采购文件要求 | 响应文件对应内容 | 是否满足 | 备注 |",
        "|---:|---|---|---|---|---|",
    ]
    for idx, item in enumerate(rows, start=1):
        lines.append(
            f"| {idx} | 审查项{idx} | {item} | 【待填写：对应材料名称/页码】 | "
            f"【待填写：满足/不满足】 | 【待填写】 |"
        )

    return "\n".join(lines)


def _build_cs_detailed_review_section(tender, packages, tender_raw: str) -> str:
    headers = ["序号", "评审项", "采购文件评分要求", "响应文件对应内容", "自评说明", "证明材料/页码"]
    tpl = getattr(tender, "detailed_review_table", None)

    fallback_rules = [
        ("技术参数", "根据招标文件技术参数逐条评审。非★项每不满足一项扣分，五项及以上不满足或★条款不满足按无效/废标处理。"),
        ("供货保证措施及运输方案", "至少展开：①供货流程及时间安排；②出库、包装措施；③运输方案及应急措施；④运输风险预防及损坏处理方案；⑤到货交接、签收验货方案。"),
        ("安装调试阶段方案", "至少展开：①人员配备；②安装措施；③调试措施；④安装调试工期保障；⑤安装调试应急预案。"),
        ("质量保证及技术措施", "至少展开：①质量保证管理体系；②质量技术人员方案及职责分工；③监督机制；④质量问题应急处理方案。"),
        ("售后服务方案", "至少展开：①售后服务方案；②售后服务流程；③售后服务标准；④售后服务人员安排；⑤售后应急处理方案。"),
        ("投标报价得分", "投标报价得分＝（评标基准价/投标报价）×价格分值。"),
    ]

    parts: list[str] = []
    for pkg in packages:
        picked = _pick_template_rows(tpl, pkg) if tpl else []
        rows: list[list[str]] = []

        if picked:
            for idx, item in enumerate(picked, start=1):
                review_item = item.get("review_item") or f"评审项{idx}"
                score_rule = (
                    item.get("score_rule")
                    or item.get("tender_requirement")
                    or item.get("_source_text")
                    or review_item
                )
                rows.append([
                    str(idx),
                    review_item,
                    score_rule,
                    "【待填写：对应章节/材料】",
                    "【待填写：如何满足该评分项】",
                    "【待填写：页码】",
                ])
        else:
            for idx, (review_item, score_rule) in enumerate(fallback_rules, start=1):
                rows.append([
                    str(idx),
                    review_item,
                    score_rule,
                    "【待填写：对应章节/材料】",
                    "【待填写：如何满足该评分项】",
                    "【待填写：页码】",
                ])

        parts.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()

def _build_cs_invalid_bid_checklist(tender, tender_raw: str) -> str:
    headers = ["序号", "无效情形", "自检结果", "备注"]
    tpl = getattr(tender, "invalid_bid_table", None)
    picked = _pick_template_rows(tpl) if tpl else []

    items: list[str] = []
    if picked:
        for row in picked:
            text = row.get("invalid_reason") or row.get("_source_text") or row.get("review_item") or ""
            text = _clean_text(text)
            if text:
                items.append(text)

    # 兜底补足本项目显著缺失的无效情形
    fallback = [
        "资格性审查任一项未通过。",
        "符合性审查任一项未通过。",
        "未在投标截止时间前上传加密电子响应文件，视为自动放弃投标。",
        "未按招标文件要求参加远程开标会。",
        "未在规定时间内完成电子响应文件在线解密。",
        "经检查数字证书无效，或投标人自身原因造成电子响应文件未能解密。",
        "未在规定时间内完成签章确认。",
        "任意一条不满足磋商文件★号条款要求。",
        "单项产品五条及以上不满足非★号条款要求。",
        "供应商所提报的技术参数没有如实填写，没有与竞争性磋商文件技术要求一一对应，只简单填写“响应/完全响应”或未逐条填写应答。",
        "供应商提报的技术参数中没有明确品牌、型号、规格、配置等。",
        "单项商品报价超单项预算。",
        "所报产品需软件而未提供正版软件，或强制节能产品未提供有效证明。",
        "未按竞争性磋商文件规定要求签字、盖章。",
        "响应文件中提供虚假材料、串通投标，或属于依法视为串通投标的情形。",
        "提交的技术参数与所提供的技术证明文件不一致。",
        "报价明显不合理且不能提供有效合理说明材料。",
        "法律法规、采购文件规定的其他无效响应情形。",
    ]

    if not items:
        items = fallback
    else:
        seen = set(items)
        for x in fallback:
            if x not in seen:
                items.append(x)
                seen.add(x)

    rows = [[str(idx), item, "【待填写：符合/不符合】", "【待填写】"] for idx, item in enumerate(items, start=1)]
    return _md_table(headers, rows)

def _detect_procurement_mode(tender, tender_raw: str) -> str:
    text = " ".join(
        [
            str(getattr(tender, "project_name", "") or ""),
            str(getattr(tender, "project_number", "") or ""),
            tender_raw or "",
        ]
    )

    if "[TP]" in text:
        return "tp"
    if "[CS]" in text:
        return "cs"

    # 再看正文关键词
    if "竞争性谈判文件" in text or "采购方式 竞争性谈判" in text or "谈判文件" in text:
        return "tp"
    if "竞争性磋商文件" in text or "采购方式 竞争性磋商" in text or "磋商文件" in text:
        return "cs"

    # 默认不要硬报错，回退到 tp/cs 其中之一
    # 当前你的项目里 TP 更常见，先保守回退 tp
    return "tp"

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


def _find_package_block(tender_raw: str, package_id: str) -> str:
    pid = str(package_id).strip()

    header_patterns = [
        rf"合同包\s*{re.escape(pid)}\s*[（(:：]?",
        rf"包\s*{re.escape(pid)}\s*[：:]",
        rf"第\s*{re.escape(pid)}\s*包",
        rf"采购包\s*{re.escape(pid)}\s*[：:]?",
    ]

    starts: list[tuple[int, int]] = []
    for pat in header_patterns:
        for m in re.finditer(pat, tender_raw):
            starts.append((m.start(), m.end()))

    if not starts:
        return ""

    starts.sort(key=lambda x: x[0])
    start, _ = starts[0]

    next_header_pat = re.compile(
        r"(合同包\s*\d+\s*[（(:：]?|包\s*\d+\s*[：:]|第\s*\d+\s*包|采购包\s*\d+\s*[：:]?)"
    )
    m_next = next_header_pat.search(tender_raw, starts[0][1])
    end = m_next.start() if m_next else len(tender_raw)

    return tender_raw[start:end]


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

def _build_tp_detail_table(tender, pkg, tender_raw: str) -> str:
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    detail_rows = _extract_tp_detail_rows(block, pkg)

    lines = [
        f"### 包{pkg.package_id}：{pkg.item_name}",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        "",
        "| 序号 | 名称 | 数量（个） | 备注 |",
        "|---:|---|---:|---|",
    ]

    if detail_rows:
        for idx, row in enumerate(detail_rows, start=1):
            lines.append(f"| {idx} | {row['name']} | {row['qty']} | {row['remark']} |")
    else:
        lines.append("| 1 | 详见采购文件装箱配置单 | 【待填写】 | 【待填写】 |")

    lines.extend(
        [
            "",
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：年 月 日】",
        ]
    )
    return "\n".join(lines)

def _extract_tp_qualification_commitment_template(tender_raw: str) -> str:
    m = re.search(
        r"四、资格承诺函(.*?)(?:五、技术偏离及详细配置明细表|五、技术偏离)",
        tender_raw,
        re.S,
    )
    if not m:
        return """黑龙江省政府采购供应商资格承诺函

（请优先从招标文件第六章提取原版模板正文；若解析失败，再由人工粘贴原版模板，禁止只保留“请插入模板”的提示语。）

同时附：
1. 营业执照或主体资格证明文件；
2. 法定代表人/单位负责人授权书；
3. 法定代表人/单位负责人及授权代表身份证明；
4. 本项目要求的医疗器械生产/经营许可、备案凭证、注册证；
5. 不得围标串标承诺函等采购文件要求的其他资格材料。""".strip()

    body = m.group(1)
    body = re.sub(r"-第\d+页-", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body

def _build_tp_combined_deviation_detail_table(tender, pkg, tender_raw: str) -> str:
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    req_rows = _extract_tp_requirement_rows(block, pkg)
    qty = _extract_tp_package_quantity(pkg, tender_raw)
    delivery_time = _extract_tp_delivery_time(pkg, tender_raw)
    delivery_place = _extract_tp_delivery_place(pkg, tender_raw)

    if not req_rows:
        req_rows = [{"requirement": "详见采购文件技术要求"}]

    lines = [
        f"### 合同包{pkg.package_id}：{pkg.item_name}",
        f"- 交货期：{delivery_time}",
        f"- 交货地点：{delivery_place}",
        "",
        "| 序号 | 货物名称 | 品牌型号、产地 | 数量/单位 | 报价(元) | 谈判文件的参数和要求 | 响应文件参数 | 偏离情况 |",
        "|---:|---|---|---|---:|---|---|---|",
    ]

    for idx, row in enumerate(req_rows, start=1):
        requirement = row["requirement"].replace("|", "/")
        lines.append(
            f"| {idx} | {pkg.item_name} | 【待填写：品牌/型号，产地】 | {qty}/台 | "
            f"【待填写】 | {requirement} | 【待填写：逐条响应参数/配置/证据】 | "
            f"【待填写：无偏离/正偏离/负偏离】 |"
        )

    lines.extend(
        [
            "",
            "说明：带“※/★”或采购文件明确为实质性条款的项目，必须逐条实质性响应，不能只写“响应/完全响应”。",
            "",
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：年 月 日】",
        ]
    )
    return "\n".join(lines)


def _build_tp_quote_summary_table(tender, packages, tender_raw: str) -> str:
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

    lines.extend(
        [
            "",
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：年 月 日】",
        ]
    )
    return "\n".join(lines)


def _build_tp_service_plan_section(packages, tender_raw: str) -> str:
    parts = []

    for pkg in packages:
        delivery_time = _extract_tp_delivery_time(pkg, tender_raw)
        delivery_place = _extract_tp_delivery_place(pkg, tender_raw)
        tender_service_text = _build_tp_service_text(pkg, tender_raw)

        parts.extend(
            [
                f"### 合同包{pkg.package_id}：{pkg.item_name}",
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
                "#### 6. 技术服务和售后服务承诺",
                tender_service_text if tender_service_text.strip() else "按采购文件售后服务要求执行。",
                "",
                "#### 7. 备件、维护与升级保障",
                "质保期内按采购文件和投标承诺提供维修、维护、巡检、升级支持；质保期外持续提供有偿维保、备件供应和技术支持，确保设备稳定运行。",
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

def _build_tp_qualification_review_section(packages) -> str:
    rows = [
        ("符合《中华人民共和国政府采购法》第二十二条规定的条件", "提交有效营业执照（或事业法人登记证或身份证等相关证明）副本复印件；或按黑龙江省资格承诺函路径提交承诺并附相应证明材料"),
        ("不存在《政府采购法实施条例》第十八条禁止情形", "承诺单位负责人同一、直接控股或管理关系冲突等情形不存在"),
        ("未被列入失信被执行人、重大税收违法失信主体、政府采购严重违法失信行为记录名单", "按采购文件要求承诺并接受查询"),
        ("法定代表人/单位负责人近三年无行贿犯罪记录", "提供书面承诺"),
        ("不是为本项目提供整体设计、规范编制、项目管理、监理、检测等服务后再参加同一项目采购活动的供应商", "提供书面承诺"),
        ("法定代表人/单位负责人授权书", "授权代表参与时提供，并签字盖章"),
        ("本项目特定资格要求", "按产品管理类别提供医疗器械生产许可证/经营许可证/备案凭证/注册证；如不按医疗器械管理则无需提供"),
        ("不得围标串标承诺", "提供承诺函，格式自拟"),
    ]

    parts = []
    for pkg in packages:
        parts.extend(
            [
                f"### 合同包{pkg.package_id}：{pkg.item_name}",
                "| 序号 | 审查项 | 招标文件要求 | 响应文件对应内容 | 是否满足 | 备注 |",
                "|---:|---|---|---|---|---|",
            ]
        )
        for idx, (item_name, requirement) in enumerate(rows, start=1):
            parts.append(
                f"| {idx} | {item_name} | {requirement} | 【待填写：对应材料名称/页码】 | "
                f"【待填写：满足/不满足】 | 【待填写】 |"
            )
        parts.append("")

    return "\n".join(parts).strip()



def _build_tp_compliance_review_section() -> str:
    rows = [
        ("投标报价", "只能有一个有效报价且不超过采购预算或最高限价，投标报价不得缺项、漏项"),
        ("投标文件规范性、符合性", "投标文件的签署、盖章、涂改、删除、插字、公章使用、格式、文字、目录等符合招标文件要求或对投标无实质性影响"),
        ("主要商务条款", "应出具满足主要商务条款的承诺书，且有法定代表人或授权代表签字并加盖单位公章"),
        ("联合体投标", "符合联合体投标相关规定；本项目不接受联合体"),
        ("技术部分实质性内容", "明确所投标的产品品牌/型号/服务内容，并对招标文件全部实质性要求作出明确响应"),
        ("其他要求", "不存在围标、串标、法律法规规定的其他无效投标情形，不存在不同投标文件文档属性中作者异常一致的情形"),
    ]

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

def _build_tp_invalid_bid_checklist() -> str:
    items = [
        "资格性审查表任一项未通过。",
        "符合性审查表任一项未通过。",
        "未按要求上传加密电子响应文件，视为自动放弃投标。",
        "未按招标文件要求参加远程开标会。",
        "未在规定时间内完成电子响应文件在线解密。",
        "经检查数字证书无效，或因供应商自身原因造成电子响应文件未能解密。",
        "未在规定时间内完成签章确认。",
        "投标报价存在多个有效报价，或超过采购预算/最高限价，或缺项、漏项。",
        "未按招标文件要求签字、盖章。",
        "技术响应未逐条对应，或带“※/★”/实质性条款存在负偏离或不满足。",
        "存在围标、串标、提供虚假材料等情形。",
        "存在不同投标文件文档属性中作者异常一致等招标文件明确列明的其他无效投标情形。",
    ]

    lines = [
        "| 序号 | 无效情形 | 自检结果 | 备注 |",
        "|---:|---|---|---|",
    ]
    for idx, item in enumerate(items, start=1):
        lines.append(f"| {idx} | {item} | 【待填写：符合/不符合】 | 【待填写】 |")
    return "\n".join(lines)



def _build_tp_deviation_table(tender, pkg, tender_raw: str) -> str:
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    req_rows = _extract_tp_requirement_rows(block, pkg)

    lines = [
        f"### 包{pkg.package_id}：{pkg.item_name}",
        f"数量：{_extract_tp_package_quantity(pkg, tender_raw)}",
        f"交货期：{_extract_tp_delivery_time(pkg, tender_raw)}",
        f"交货地点：{_extract_tp_delivery_place(pkg, tender_raw)}",
        "",
        "| 序号 | 谈判文件技术要求 | 响应内容 | 偏离情况 |",
        "|---:|---|---|---|",
    ]

    if req_rows:
        for idx, row in enumerate(req_rows, start=1):
            lines.append(
                f"| {idx} | {row['requirement'].replace('|', '/')} | "
                f"【待填写：品牌/型号/规格/配置及逐条响应】 | "
                f"【待填写：无偏离/正偏离/负偏离】 |"
            )
    else:
        lines.append("| 1 | 详见采购文件技术要求 | 【待填写：逐条响应】 | 【待填写】 |")

    lines.extend(
        [
            "",
            "说明：带“※/★”或采购文件明确为实质性条款的项目，必须逐条实质性响应，不能只写“响应/完全响应”。",
            "",
            "供应商全称：【待填写：投标人名称】",
            "日期：【待填写：年 月 日】",
        ]
    )
    return "\n".join(lines)

def _build_tp_appendix_section(packages, tender_raw: str) -> str:
    parts = [
        "报价书附件必须至少包含以下内容：",
        "1. 产品主要技术参数明细表及报价表；",
        "2. 技术服务和售后服务的内容及措施；",
        "",
        "报价书附件可补充以下材料：",
        "1. 产品详细说明书或产品样本；",
        "2. 产品制造、验收标准；",
        "3. 详细交货清单；",
        "4. 特殊工具及备件清单；",
        "5. 供应商推荐的供选择的配套货物表；",
        "6. 其他辅助性说明材料；",
        "",
    ]

    for pkg in packages:
        parts.extend(
            [
                f"### 包{pkg.package_id}：{pkg.item_name}",
                f"- 交货期：{_extract_tp_delivery_time(pkg, tender_raw)}",
                f"- 交货地点：{_extract_tp_delivery_place(pkg, tender_raw)}",
                "- 技术服务和售后服务承诺：",
                _build_tp_service_text(pkg, tender_raw),
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

import re


def _extract_tp_summary_rows(tender_raw: str) -> list[dict]:
    rows = []

    # 包摘要表
    pattern = re.compile(
        r"(?P<pkg>\d+)\s+"
        r"(?P<name>.+?)\s+"
        r"(?P<qty>\d+(?:\.\d+)?)\s+"
        r"详见采购文件\s+"
        r"(?P<budget>[0-9,]+(?:\.\d+)?)",
        re.S,
    )
    for m in pattern.finditer(tender_raw):
        rows.append(
            {
                "package_id": m.group("pkg").strip(),
                "item_name": " ".join(m.group("name").split()),
                "quantity": m.group("qty").strip(),
                "budget": m.group("budget").replace(",", "").strip(),
            }
        )

    return rows


def _find_tp_summary_row(tender_raw: str, package_id: str) -> dict | None:
    for row in _extract_tp_summary_rows(tender_raw):
        if row["package_id"] == str(package_id):
            return row
    return None

def _extract_tp_package_quantity(pkg, tender_raw: str) -> str:
    # TP 项目优先取第二章各包技术标准与要求表里的数量（更准确）
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    m = re.search(r"数量\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:台)?", block)
    if m:
        return m.group(1).strip()

    row = _find_tp_summary_row(tender_raw, pkg.package_id)
    if row and row.get("quantity"):
        return row["quantity"]

    q = getattr(pkg, "quantity", None)
    if q not in (None, ""):
        return str(q).strip()

    return "【待填写：数量】"


def _extract_tp_package_budget(pkg, tender_raw: str) -> str:
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
    m = re.search(
        rf"合同包{re.escape(str(pkg.package_id))}[（(].*?[）)][:：]?\s*([^\n]+送达指定地点)",
        tender_raw,
    )
    if m:
        return " ".join(m.group(1).split())
    return "按采购文件要求"


def _extract_tp_delivery_place(pkg, tender_raw: str) -> str:
    m = re.search(
        rf"合同包{re.escape(str(pkg.package_id))}[（(].*?[）)][:：]?\s*([^\n]+地点)",
        tender_raw,
    )
    if m:
        return " ".join(m.group(1).split())
    return "采购人指定地点"


def _find_tp_package_block(tender_raw: str, package_id: str) -> str:
    start_pat = re.compile(rf"合同包{re.escape(str(package_id))}[（(]")
    all_pkg = list(re.finditer(r"合同包\d+[（(]", tender_raw))

    start = None
    end = len(tender_raw)

    for i, m in enumerate(all_pkg):
        if start_pat.match(m.group(0)):
            start = m.start()
            if i + 1 < len(all_pkg):
                end = all_pkg[i + 1].start()
            break

    if start is None:
        return tender_raw

    return tender_raw[start:end]


def _extract_tp_detail_rows(block: str, pkg) -> list[dict]:
    """
    从包正文中提取‘装箱配置单/配置清单’。
    兼容类似：
    四、装箱配置单：
    1 主机 1
    2 电源线 1
    """
    rows = []

    m = re.search(r"四、装箱配置单[：:]?(.*?)(?:五、质保|六、售后服务要求|七、)", block, re.S)
    if not m:
        return rows

    lines = [line.strip() for line in m.group(1).splitlines() if line.strip()]
    for line in lines:
        mm = re.match(r"^\d+\s+(.+?)\s+(\d+)\s*$", line)
        if mm:
            rows.append(
                {
                    "name": mm.group(1).strip(),
                    "qty": mm.group(2).strip(),
                    "remark": "",
                }
            )

    return rows


def _extract_tp_requirement_rows(block: str, pkg) -> list[dict]:
    """
    提取‘三、技术参数’里的逐条要求。
    兼容：
    1、xxx
    2、xxx
    ※1、xxx
    ★2、xxx
    """
    rows = []
    m = re.search(r"三、技术参数[：:]?(.*?)(?:四、装箱配置单|五、质保|六、售后服务要求)", block, re.S)
    if not m:
        return rows

    raw_lines = [line.strip() for line in m.group(1).splitlines() if line.strip()]
    merged = []

    for line in raw_lines:
        if re.match(r"^(?:[※★]?\d+[、.]|[※★]?\d+\s*[、.])", line):
            merged.append(line)
        else:
            if merged:
                merged[-1] += " " + line

    for item in merged:
        rows.append({"requirement": " ".join(item.split())})

    return rows

def _build_tp_service_text(pkg, tender_raw: str) -> str:
    block = _find_tp_package_block(tender_raw, pkg.package_id)
    m = re.search(r"六、售后服务要求[：:]?(.*?)(?:说明\s*打|合同包\d+|$)", block, re.S)
    if not m:
        return "按采购文件售后服务要求执行。"

    raw_lines = [line.strip() for line in m.group(1).splitlines() if line.strip()]
    merged = []

    for line in raw_lines:
        if re.match(r"^\d+[、.]", line):
            merged.append(line)
        else:
            if merged:
                merged[-1] += " " + line

    if not merged:
        return "按采购文件售后服务要求执行。"

    return "\n".join(f"- {x}" for x in merged)



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


def _default_cs_qualification_rows() -> list[tuple[str, str]]:
    return [
        ("资格承诺", "符合《中华人民共和国政府采购法》第二十二条规定，并按采购文件要求提交资格承诺函或对应证明材料"),
        ("营业执照", "提交有效营业执照或事业单位法人证书等主体资格证明材料"),
        ("信用记录", "未被列入失信被执行人、重大税收违法失信主体、政府采购严重违法失信行为记录名单"),
        ("法定代表人授权", "法定代表人/单位负责人授权代表参加时提交授权书及身份证明"),
        ("特定资格", "按本项目特定资格要求提交许可证、备案凭证、注册证、授权书等材料"),
    ]


def _default_cs_compliance_rows() -> list[tuple[str, str]]:
    return [
        ("响应文件完整性", "响应文件的格式、签署、盖章、目录、页码和响应内容应符合采购文件要求"),
        ("报价有效性", "报价唯一且不超过预算/最高限价，不得缺项、漏项"),
        ("实质性响应", "对采购文件带※/★及其他实质性条款逐条明确响应，不得负偏离"),
        ("交货和质保", "交货期、交货地点、质保期、售后响应等主要商务条款满足采购文件要求"),
        ("其他无效情形", "不存在围标串标、弄虚作假及采购文件列明的其他无效响应情形"),
    ]


def _build_tp_sections(
    tender,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list | None = None,
) -> list:
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

    combined_parts = []
    for pkg in packages:
        combined_parts.append(_build_tp_combined_deviation_detail_table(tender, pkg, tender_raw))

    sections.append(
        BidDocumentSection(
            section_title="五、技术偏离及详细配置明细表",
            content="\n\n".join(combined_parts).strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="六、技术服务和售后服务的内容及措施",
            content=_build_tp_service_plan_section(packages, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="七、资格性审查响应对照表",
            content=_build_tp_qualification_review_section(packages),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="八、符合性审查响应对照表",
            content=_build_tp_compliance_review_section(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="九、投标无效情形汇总及自检表",
            content=_build_tp_invalid_bid_checklist(),
        )
    )

    return sections

def _build_cs_sections(
    tender,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list | None = None,
) -> list:
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
磋商日期：【待填写：磋商日期】
""".strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="二、首轮报价表",
            content="采用电子招投标的项目无需编制该表格，按投标客户端报价部分填写。",
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="三、分项报价表",
            content="采用电子招投标的项目无需编制该表格，按投标客户端报价部分填写。",
        )
    )

    deviation_parts: list[str] = []
    for pkg in packages:
        deviation_parts.append(_build_cs_pkg_deviation_table(tender, pkg, tender_raw))

    sections.append(
        BidDocumentSection(
            section_title="四、技术偏离及详细配置明细表",
            content="\n\n".join(deviation_parts).strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="五、技术服务和售后服务的内容及措施",
            content=_build_cs_service_section(packages, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="六、法定代表人/单位负责人授权书",
            content=f"""
（报价单位全称）法定代表人/单位负责人授权【待填写：授权代表姓名】为供应商代表，
参加贵处组织的 {tender.project_name}（项目编号：{tender.project_number}）竞争性磋商，
全权处理本活动中的一切事宜。

法定代表人/单位负责人签字或盖章：【待填写】
授权代表签字：【待填写】
供应商全称（公章）：【待填写：投标人名称】
日期：【待填写：年 月 日】
""".strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="七、资格性审查响应对照表",
            content=_build_cs_qualification_review_section(tender, packages, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="八、符合性审查响应对照表",
            content=_build_cs_compliance_review_section(tender, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="九、详细评审响应对照表",
            content=_build_cs_detailed_review_section(tender, packages, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="十、投标无效情形汇总及自检表",
            content=_build_cs_invalid_bid_checklist(tender, tender_raw),
        )
    )

    return sections

# def _find_package_block(tender_raw: str, package_id: str) -> str:
#     markers = list(re.finditer(r"第[一二三四五六七八九十]+包|包\s*号[:：]?\s*\d+|手术用头架|X射线血液辐照设备", tender_raw))
#     # 简化兜底：优先按合同包标题切；如果当前 raw 已经被预处理，可以按 item_name 附近截取
#     summary_row = _find_summary_row(tender_raw, package_id)
#     if not summary_row:
#         return tender_raw
#     name = summary_row["item_name"]
#     pos = tender_raw.find(name)
#     if pos < 0:
#         return tender_raw
#     tail = tender_raw[pos: pos + 12000]
#     return tail


def _extract_numbered_points(block: str) -> list[tuple[str, str]]:
    """
    提取类似：
    1.1 适用范围：国内
    3.2 ...
    4.1 ...
    """
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    merged: list[str] = []
    for line in lines:
        if re.match(r"^\d+(?:\.\d+)+", line):
            merged.append(line)
        else:
            if merged:
                merged[-1] += " " + line
            else:
                merged.append(line)

    result: list[tuple[str, str]] = []
    for item in merged:
        m = re.match(r"^(\d+(?:\.\d+)+)\s*(.*)$", item)
        if m:
            code = m.group(1).strip()
            text = " ".join(m.group(2).split())
            result.append((code, text))
    return result


def _extract_cs_requirement_rows(pkg, tender_raw: str) -> list[dict]:
    block = _find_package_block(tender_raw, pkg.package_id)
    if not block:
        return [{"seq": "1", "item_name": pkg.item_name, "requirement": "详见采购文件技术要求"}]

    captured: list[str] = []
    patterns = [
        r"2\s*设备配置与配件\s*(.*?)(?:4\s*售后服务|说明\s*打|合同包\s*\d+|$)",
        r"3\s*技术参数与性能要求[（(].*?[)）]?\s*(.*?)(?:4\s*售后服务|说明\s*打|合同包\s*\d+|$)",
        r"技术标准与要求\s*(.*?)(?:4\s*售后服务|说明\s*打|合同包\s*\d+|$)",
    ]

    for pat in patterns:
        m = re.search(pat, block, re.S)
        if m and (m.group(1) or "").strip():
            captured.append(m.group(1).strip())

    tech_text = "\n".join(captured).strip()
    if not tech_text:
        return [{"seq": "1", "item_name": pkg.item_name, "requirement": "详见采购文件技术要求"}]

    lines = [_clean_text(x) for x in tech_text.splitlines() if _clean_text(x)]
    merged: list[str] = []

    for line in lines:
        if re.match(r"^\*?\d+(?:\.\d+)+\s*", line):
            merged.append(line)
        elif merged:
            merged[-1] += " " + line

    rows: list[dict] = []
    for idx, item in enumerate(merged, start=1):
        item = _clean_text(item)
        if not item:
            continue
        rows.append({
            "seq": str(idx),
            "item_name": pkg.item_name,
            "requirement": item,
        })

    return rows or [{"seq": "1", "item_name": pkg.item_name, "requirement": "详见采购文件技术要求"}]


def _build_cs_pkg_deviation_table(tender, pkg, tender_raw: str) -> str:
    qty = _extract_package_quantity(pkg, tender_raw)
    rows = _extract_cs_requirement_rows(pkg, tender_raw)

    lines = [
        f"### 包{pkg.package_id}：{pkg.item_name}",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        f"数量：{qty}",
        f"交货期：{_extract_delivery_time(pkg, tender_raw)}",
        f"交货地点：{_extract_delivery_place(pkg, tender_raw)}",
        "",
        "| 序号 | 服务名称 | 磋商文件的服务需求 | 响应文件响应情况 | 偏离情况 |",
        "|---:|---|---|---|---|",
    ]

    generic_hit = len(rows) == 1 and rows[0].get("requirement") == "详见采购文件技术要求"
    for row in rows:
        response_placeholder = "【待填写：品牌/型号/规格/配置及逐条响应】"
        if generic_hit:
            response_placeholder = "【待补：按采购文件逐条拆解参数后填写品牌/型号/配置/偏离说明】"
        lines.append(
            f"| {row['seq']} | {row['item_name']} | {row['requirement']} | "
            f"{response_placeholder} | 【待填写：无偏离/正偏离/负偏离】 |"
        )

    lines.extend([
        "",
        "说明：带“※/★”或采购文件明确为实质性条款的项目，必须逐条实质性响应，不能只写“响应/完全响应”。",
        "",
        "供应商全称：【待填写：投标人名称】",
        "日期：【待填写：年 月 日】",
    ])
    return "\n".join(lines)

def _extract_cs_service_points(pkg, tender_raw: str) -> list[str]:
    block = _find_package_block(tender_raw, pkg.package_id)
    if not block:
        return []

    service_text = ""
    patterns = [
        r"六、售后服务要求[：:]?(.*?)(?:说明\s*打|评分标准|商务要求|合同包\s*\d+|包\s*\d+[：:]|第\s*\d+\s*包|$)",
        r"售后服务要求[：:]?(.*?)(?:说明\s*打|评分标准|商务要求|合同包\s*\d+|包\s*\d+[：:]|第\s*\d+\s*包|$)",
        r"服务要求[：:]?(.*?)(?:说明\s*打|评分标准|商务要求|合同包\s*\d+|包\s*\d+[：:]|第\s*\d+\s*包|$)",
        r"商务要求[：:]?(.*?)(?:评分标准|合同包\s*\d+|包\s*\d+[：:]|第\s*\d+\s*包|$)",
    ]

    for pat in patterns:
        m = re.search(pat, block, re.S)
        if m and (m.group(1) or "").strip():
            service_text = m.group(1).strip()
            break

    if not service_text:
        return []

    raw_lines = [line.strip() for line in service_text.splitlines() if line.strip()]
    merged: list[str] = []

    for line in raw_lines:
        if re.match(r"^(?:\d+[、.：:]|[（(]?\d+[）)]|[一二三四五六七八九十]+、)", line):
            merged.append(line)
        else:
            if merged:
                merged[-1] += " " + line

    cleaned = []
    for item in merged:
        s = " ".join(item.split())
        if s and s not in {"售后服务要求", "服务要求", "商务要求"}:
            cleaned.append(s.replace("|", "/"))

    return cleaned


def _build_cs_service_section(packages, tender_raw: str) -> str:
    parts: list[str] = []

    for pkg in packages:
        qty = _extract_package_quantity(pkg, tender_raw)
        delivery_time = _extract_delivery_time(pkg, tender_raw)
        delivery_place = _extract_delivery_place(pkg, tender_raw)

        parts.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            f"数量：{qty}",
            f"交货期：{delivery_time}",
            f"交货地点：{delivery_place}",
            "",
            "#### 1. 供货组织措施",
            "1）供货流程及时间安排：中标后立即启动合同分解、排产锁货、发运审批、到货预约四级计划，形成节点进度表并明确责任人。",
            "2）产品出库、包装措施：发货前完成数量、型号、外观、随机附件复核；包装按原厂标准执行，落实防震、防潮、防压、防磕碰措施。",
            "3）运输方案及应急措施：采用专车或合规物流运输，全程跟踪；如遇天气、道路、航班等异常情况，立即启动改期或备用线路方案。",
            "4）运输风险预防及损坏处理方案：投保运输险，到货发现破损、受潮、缺件时，现场拍照取证并同步启动补发、换货或整改流程。",
            "5）到达指定地点后交接、签收验货方案：设备到达后由项目经理会同采购人完成数量清点、外观检查、随机资料核验及签收确认。",
            "",
            "#### 2. 安装调试阶段方案",
            "1）人员配备：安排项目经理、安装工程师、调试工程师、培训工程师，明确岗位职责和联系方式。",
            "2）安装措施：按场地条件进行开箱核验、设备定位、部件组装、通电前检查，确保安装过程规范可控。",
            "3）调试措施：完成功能调试、参数校准、联机测试和试运行，形成调试记录。",
            "4）安装调试工期保障措施：设备到货后按采购人通知及时进场，倒排安装调试计划，保障在约定时限内完成。",
            "5）安装调试应急预案：对场地条件异常、配件缺失、电源环境不符、联机故障等情况设置应急处理机制。",
            "",
            "#### 3. 培训措施",
            "1）对操作人员开展开关机、标准操作流程、注意事项、常见问题处理等培训。",
            "2）对管理人员开展设备管理、维护要求、风险控制、记录留存等培训。",
            "3）培训完成后组织现场答疑和操作确认，并形成培训签到和培训记录。",
            "",
            "#### 4. 验收配合措施",
            "1）到货验收：配合采购人对外包装、数量、随机附件、资料进行验收。",
            "2）安装验收：提交安装调试记录，配合完成功能配置验收。",
            "3）技术验收：按招标文件技术参数逐项核验，并提供相应证明资料。",
            "4）资料移交：提交合格证、说明书、装箱单、注册证/备案凭证、配置清单、保修资料等。",
            "",
            "#### 5. 质量保证及技术措施",
            "1）建立项目质量保证管理体系，明确项目经理总负责制。",
            "2）明确质量技术人员方案及职责分工，形成安装、调试、培训、售后岗位责任表。",
            "3）建立监督机制，对发货、到货、安装、调试、验收等关键节点进行复核。",
            "4）如发生质量问题，第一时间隔离问题设备/配件、分析原因并落实纠正和补救措施。",
            "",
            "#### 6. 售后服务承诺",
            "1）售后服务方案：按采购文件约定的质保年限和服务要求执行。",
            "2）售后服务流程：报修受理→远程诊断→现场服务→故障排除→回访闭环。",
            "3）售后服务标准：按厂家及行业规范提供维保、巡检、升级和备件保障服务。",
            "4）售后服务人员安排：明确售后负责人、工程师及联系电话。",
            "5）售后应急处理方案：对停机、核心部件异常等情况启动快速响应机制。",
            "",
        ])

    parts.extend([
        "供应商全称：【待填写：投标人名称】",
        "日期：【待填写：年 月 日】",
    ])
    return "\n".join(parts)




def build_format_driven_sections(
    tender,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list | None = None,
) -> list:
    """
    对外统一导出函数：
    - CS 项目 -> 走 _build_cs_sections
    - TP 项目 -> 走 _build_tp_sections
    """
    mode = _detect_procurement_mode(tender, tender_raw)

    if mode == "tp":
        return _build_tp_sections(
            tender=tender,
            tender_raw=tender_raw,
            products=products,
            active_packages=active_packages,
        )

    if mode == "cs":
        return _build_cs_sections(
            tender=tender,
            tender_raw=tender_raw,
            products=products,
            active_packages=active_packages,
        )

    # 未识别时，默认按 tp 处理
    return _build_tp_sections(
        tender=tender,
        tender_raw=tender_raw,
        products=products,
        active_packages=active_packages,
    )