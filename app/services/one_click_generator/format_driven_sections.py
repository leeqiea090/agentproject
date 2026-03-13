from __future__ import annotations

import re

from app.schemas import BidDocumentSection, TenderDocument, ProcurementPackage


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
    matches = list(re.finditer(r"合同包(\d+)（", tender_raw))
    for idx, m in enumerate(matches):
        if m.group(1) == str(package_id):
            start = m.start()
            if idx + 1 < len(matches):
                end = matches[idx + 1].start()
            else:
                tail = re.search(r"第三章\s*供应商须知", tender_raw[m.end():])
                end = m.end() + tail.start() if tail else len(tender_raw)
            return tender_raw[start:end]
    return ""


def _extract_detail_quantity(pkg: ProcurementPackage, tender_raw: str) -> str:
    block = _find_package_block(tender_raw, pkg.package_id)
    if block:
        m = re.search(r"二、数量[：:]\s*([0-9]+(?:\.[0-9]+)?)", block)
        if m:
            return _normalize_number_text(m.group(1))
    return _normalize_number_text(getattr(pkg, "quantity", "")) or "【待填写：数量】"


def _extract_delivery_time(package_id: str, tender_raw: str) -> str:
    m = re.search(rf"合同包{re.escape(str(package_id))}（[^\n]*?[）)]：\s*([^\n]+)", tender_raw)
    return m.group(1).strip() if m else "按采购文件要求"


def _extract_delivery_place(package_id: str, tender_raw: str) -> str:
    m = re.search(rf"合同包{re.escape(str(package_id))}（[^\n]*?[）)]：\s*([^\n]+)", tender_raw)
    return m.group(1).strip() if m else "按采购文件要求"


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


def build_format_driven_sections(
    tender: TenderDocument,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list[ProcurementPackage] | None = None,
) -> list[BidDocumentSection]:
    packages = active_packages or tender.packages
    sections: list[BidDocumentSection] = []

    package_brief = []
    for pkg in packages:
        qty = _extract_detail_quantity(pkg, tender_raw)
        delivery = _extract_delivery_time(pkg.package_id, tender_raw)
        package_brief.append(
            f"包{pkg.package_id}：{pkg.item_name}；数量：{qty}；预算：{_budget_text(pkg)}元；交货期：{delivery}"
        )

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
谈判日期：【待填写：谈判日期】
""".strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="二、报价书",
            content=(
                f"（供应商全称）授权【待填写：授权代表姓名】为响应供应商代表，参加贵方组织的"
                f"（{tender.project_number}、{tender.project_name}）谈判有关活动，并对本项目进行报价。\n\n"
                f"1. 提供供应商须知规定的全部响应文件。\n"
                f"2. 报价总价为：【待填写：投标总报价】元人民币。\n"
                f"3. 保证遵守竞争性谈判文件中的有关规定。\n"
                f"4. 保证忠实执行双方签订的政府采购合同并承担相应责任义务。\n"
                f"5. 与本项目有关的采购包摘要如下：\n"
                + "\n".join(f"- {x}" for x in package_brief)
                + "\n\n地址：【待填写】 邮编：【待填写】 电话：【待填写】 传真：【待填写】\n"
                  "供应商全称：【待填写：投标人名称】\n"
                  "日期：【待填写：年 月 日】"
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="三、报价一览表",
            content=_build_quote_summary_table(tender, packages, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="四、资格承诺函",
            content="""
请直接插入采购文件附件原版《黑龙江省政府采购供应商资格承诺函》，并按 CA / 签章要求完成签署。

同时附：
1. 供应商主体资格证明文件；
2. 法定代表人/单位负责人授权书；
3. 法定代表人/单位负责人及授权代表身份证明；
4. 所投产品对应的医疗器械生产/经营许可、备案凭证、产品注册证等特定资质文件；
5. 围标串标承诺函等采购文件要求的其他资格性材料。
""".strip(),
        )
    )

    merged_table_parts: list[str] = []
    for pkg in packages:
        merged_table_parts.append(_build_pkg_deviation_table(tender, pkg, tender_raw))

    sections.append(
        BidDocumentSection(
            section_title="五、技术偏离及详细配置明细表",
            content="\n\n".join(merged_table_parts).strip(),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="六、技术服务和售后服务的内容及措施",
            content=_build_service_section(packages, tender_raw),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="七、报价书附件",
            content="""
报价书附件必须至少包含以下内容：
1. 产品主要技术参数明细表及报价表；
2. 技术服务和售后服务的内容及措施。

报价书附件可补充以下材料：
1. 产品详细说明书或产品样本；
2. 产品制造、验收标准；
3. 详细交货清单；
4. 特殊工具及备件清单；
5. 供应商推荐的配套货物表；
6. 其他辅助性说明材料。
""".strip(),
        )
    )

    return sections