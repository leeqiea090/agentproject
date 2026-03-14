"""竞争性磋商格式驱动章节生成。"""
from __future__ import annotations

from .common import *  # noqa: F401,F403

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


def _build_cs_compliance_review_section(tender, tender_raw: str) -> str:
    headers = ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"]
    tpl = getattr(tender, "compliance_review_table", None)

    rows_data: list[tuple[str, str]] = []
    picked = _pick_template_rows(tpl) if tpl else []

    for item in picked:
        review_item = (
            item.get("review_item")
            or item.get("审查项")
            or item.get("_source_text")
            or ""
        )
        tender_requirement = (
            item.get("tender_requirement")
            or item.get("采购文件要求")
            or item.get("招标文件要求")
            or item.get("_source_text")
            or ""
        )
        review_item = _clean_text(review_item)
        tender_requirement = _clean_text(tender_requirement)

        if not review_item or not tender_requirement:
            continue
        if review_item in {"表二符合性审查表", "符合性审查表"}:
            continue

        rows_data.append((review_item, tender_requirement))

    if not rows_data:
        rows_data = [
            (
                "投标报价",
                "投标报价（包括分项报价，投标总报价）只能有一个有效报价且不超过采购预算或最高限价，投标报价不得缺项、漏项。投标报价经评审认定明显低于成本价，且供应商无法在规定时间内提供有效且合理的证明材料以说明其报价合理性、评标委员会认为供应商提供的证明材料不满足要求或不足以说明其报价合理性，则对该供应商的响应文件作无效处理。",
            ),
            (
                "投标文件规范性、符合性",
                "投标文件的签署、盖章、涂改、删除、插字、公章使用等符合招标文件要求；投标文件文件的格式、文字、目录等符合招标文件要求或对投标无实质性影响。",
            ),
            (
                "主要商务条款",
                "审查投标人出具的“满足主要商务条款的承诺书”，且进行签署或盖章。",
            ),
            (
                "联合体投标",
                "符合关于联合体投标的相关规定。",
            ),
            (
                "技术部分实质性内容",
                "1. 明确所投标的产品品牌；2. 投标文件应当对招标文件提出的要求和条件作出明确响应并满足招标文件全部实质性要求。",
            ),
            (
                "其他要求",
                "招标文件要求的其他无效投标情形；围标、串标和法律法规规定的其它无效投标条款。",
            ),
        ]

    rows = [
        [
            str(idx),
            item_name,
            rule,
            "【待填写：对应材料名称/页码】",
            "【待填写：满足/不满足】",
            "【待填写】",
        ]
        for idx, (item_name, rule) in enumerate(rows_data, start=1)
    ]
    return _md_table(headers, rows)


def _build_cs_detailed_review_section(tender, packages, tender_raw: str) -> str:
    headers = ["序号", "评审项", "采购文件评分要求", "响应文件对应内容", "自评说明", "证明材料/页码"]
    tpl = getattr(tender, "detailed_review_table", None)

    fallback_rows = [
        (
            "技术参数",
            "根据招标文件技术参数进行逐条评审。所投货物一般技术指标、参数全部满足招标文件技术参数要求得20分，如有一项非标记星号项的技术参数不满足招标文件要求的在20分基础上扣5分，五项（含五项）以上非标记星号项的技术参数不满足招标文件要求的按废标处理；重要配置功能缺失的按废标处理；不满足星号条款要求的按废标处理。",
        ),
        (
            "供货保证措施及运输方案",
            "评标委员会根据供应商提供的供货保证措施及运输方案，从以下5方面进行评审：①供货流程及时间安排；②产品的出库、包装措施；③产品的运输方案及应急措施；④产品的运输风险预防措施及运输过程中出现损坏的处理方案；⑤产品到达指定地点后交接、签收验货方案。以上五项内容无缺项得15分，每缺一项扣3分，每项内容中有一处缺陷的扣0.5分，每项最多扣1分。",
        ),
        (
            "安装调试阶段方案",
            "评标委员会根据供应商提供的安装调试阶段方案，从以下5方面进行评审：①人员配备；②安装措施；③调试措施；④安装调试的工期保障措施；⑤安装调试的应急预案。以上五项内容无缺项得15分，每缺一项扣3分，每项内容中有一处缺陷的扣0.5分，每项最多扣1分。",
        ),
        (
            "质量保证及技术措施",
            "评标委员会根据供应商提供的质量保证及技术措施方案，从以下4方面进行评审：①质量保证管理体系；②质量技术人员方案及职责分工；③监督机制；④质量问题应急处理方案。以上四项内容无缺项得10分，每缺一项扣2.5分，每项内容中有一处缺陷的扣0.5分，每项最多扣1分。",
        ),
        (
            "售后服务方案",
            "评标委员会根据供应商提供的售后服务方案，从以下5方面进行评审：①售后服务方案；②售后服务流程；③售后服务标准；④售后服务人员安排；⑤售后应急处理方案。以上五项内容无缺项得10分，每缺一项扣2分，每项内容中有一处缺陷的扣0.5分，每项最多扣1分。",
        ),
        (
            "投标报价得分",
            "投标报价得分＝（评标基准价/投标报价）×价格分值。满足招标文件要求且投标价格最低的投标报价为评标基准价。最低报价不是中标的唯一依据。因落实政府采购政策进行价格调整的，以调整后的价格计算评标基准价和投标报价。",
        ),
    ]

    parts: list[str] = []
    for pkg in packages:
        source_rows: list[tuple[str, str]] = []
        picked = _pick_template_rows(tpl, pkg) if tpl else []

        for item in picked:
            review_item = (
                item.get("review_item")
                or item.get("评审项")
                or item.get("_source_text")
                or ""
            )
            score_rule = (
                item.get("score_rule")
                or item.get("采购文件评分要求")
                or item.get("评审标准")
                or item.get("_source_text")
                or ""
            )

            review_item = _clean_text(review_item)
            score_rule = _clean_text(score_rule)

            if not review_item or not score_rule:
                continue
            if review_item in {"分值构成", "评审因素", "评审标准", "技术部分", "商务部分", "报价得分"}:
                continue

            source_rows.append((review_item, score_rule))

        if not source_rows:
            source_rows = fallback_rows

        rows = [
            [
                str(idx),
                item_name,
                rule,
                "【待填写：对应章节/材料】",
                "【待填写：如何满足该评分项】",
                "【待填写：页码】",
            ]
            for idx, (item_name, rule) in enumerate(source_rows, start=1)
        ]

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
    for row in picked:
        text = row.get("invalid_reason") or row.get("_source_text") or row.get("review_item") or ""
        text = _clean_text(text)
        if _is_valid_invalid_item(text):
            items.append(text)

    fallback = [
        "资格性审查任一项未通过。",
        "符合性审查任一项未通过。",
        "非★条款有重大偏离经磋商小组专家认定无法满足竞争性磋商文件需求的。",
        "未按竞争性磋商文件规定要求签字、盖章的。",
        "响应文件中提供虚假材料的。",
        "提交的技术参数与所提供的技术证明文件不一致的。",
        "所报项目在实际运行中，其使用成本过高、使用条件苛刻，经磋商小组确定后不能被采购人接受的。",
        "法定代表人/单位负责人授权书无法定代表人/单位负责人签字或没有加盖公章的。",
        "参加政府采购活动前三年内，在经营活动中有重大违法记录的。",
        "供应商对采购人、代理机构、磋商小组及其工作人员施加影响，有碍公平、公正的。",
        "单位负责人为同一人或者存在直接控股、管理关系的不同供应商参与本项目同一合同项下投标的。",
        "属于串通投标，或者依法被视为串通投标的。",
        "排在前面的入围候选供应商报价明显不合理或者低于成本，且不能作出书面说明并提供相关证明材料的。",
        "未在投标截止时间前上传加密电子响应文件的。",
        "未按要求参加开标/磋商并完成在线解密、签章确认的。",
        "按有关法律、法规、规章规定属于响应无效的其他情形。",
    ]

    seen = set()
    cleaned_items: list[str] = []
    for x in items + fallback:
        x = _clean_text(x)
        if not x or x in seen:
            continue
        seen.add(x)
        cleaned_items.append(x)

    rows = [
        [str(idx), item, "【待填写：符合/不符合】", "【待填写】"]
        for idx, item in enumerate(cleaned_items, start=1)
    ]
    return _md_table(headers, rows)


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
        return [
            {
                "seq": "1",
                "item_name": pkg.item_name,
                "requirement": "【待人工根据采购文件逐条补录技术参数，禁止仅写“响应/完全响应”】",
            }
        ]

    m = re.search(
        r"(?:附表一[：:].*?(?:参数性质\s*序号\s*具体技术(?:\(参数\))?要求)?)(.*?)(?:说明\s*打[“\"★*]|第三章|合同包\s*\d+|采购包\s*\d+|$)",
        block,
        re.S,
    )
    scope = (m.group(1) if m else block) or block

    raw_lines = [_clean_text(x) for x in scope.splitlines() if _clean_text(x)]
    merged: list[str] = []

    for s in raw_lines:
        s = s.lstrip("*").strip()

        if re.match(r"^(?:设备名称：|[一二三四五六七八九十]+、|[※★]?\d+[、.]|[※★]?\d+(?:\.\d+)+)\s*", s):
            merged.append(s)
            continue

        if merged and not re.match(
            r"^(?:合同包\s*\d+|采购包\s*\d+|附表一[：:]|参数性质|序号\s+要求|序号\s+具体技术|说明\s*打|第三章|第四章|第五章|第六章)$",
            s,
        ):
            merged[-1] += " " + s

    rows: list[dict] = []
    seen_keys: set[str] = set()

    for item in merged:
        item = _clean_text(item)
        if not item:
            continue

        m_key = re.match(r"^(设备名称：|[一二三四五六七八九十]+、|[※★]?\d+(?:\.\d+)+|[※★]?\d+[、.])", item)
        row_key = m_key.group(1) if m_key else item[:40]

        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)

        rows.append(
            {
                "seq": str(len(rows) + 1),
                "item_name": pkg.item_name,
                "requirement": item,
            }
        )

    if rows:
        return rows

    return [
        {
            "seq": "1",
            "item_name": pkg.item_name,
            "requirement": "【待人工根据采购文件逐条补录技术参数，禁止仅写“响应/完全响应”】",
        }
    ]


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

    for row in rows:
        lines.append(
            f"| {row['seq']} | {row['item_name']} | {row['requirement']} | "
            f"【待填写：品牌/型号/规格/配置及逐条响应】 | "
            f"【待填写：无偏离/正偏离/负偏离】 |"
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
        raw_service_points = _extract_cs_service_points(pkg, tender_raw)

        parts.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            f"数量：{qty}",
            f"交货期：{delivery_time}",
            f"交货地点：{delivery_place}",
            "",
            "#### 1. 供货保证措施及运输方案",
            "1）供货流程及时间安排：中标后立即启动合同分解、排产锁货、发运审批、到货预约四级计划，形成节点进度表并明确责任人。",
            "2）产品出库、包装措施：发货前完成数量、型号、外观、随机附件复核；包装按原厂标准执行，落实防震、防潮、防压、防磕碰措施。",
            "3）产品的运输方案及应急措施：采用专车或合规物流运输，全程跟踪；如遇天气、道路、航班等异常情况，立即启动改期或备用线路方案。",
            "4）产品的运输风险预防措施及运输过程中出现损坏的处理方案：投保运输险，到货发现破损、受潮、缺件时，现场拍照取证并同步启动补发、换货或整改流程。",
            "5）产品到达指定地点后交接、签收验货方案：设备到达后由项目经理会同采购人完成数量清点、外观检查、随机资料核验及签收确认。",
            "",
            "#### 2. 安装调试阶段方案",
            "1）人员配备：安排项目经理、安装工程师、调试工程师、培训工程师，明确岗位职责和联系方式。",
            "2）安装措施：按场地条件进行开箱核验、设备定位、部件组装、通电前检查，确保安装过程规范可控。",
            "3）调试措施：完成功能调试、参数校准、联机测试和试运行，形成调试记录。",
            "4）安装调试的工期保障措施：设备到货后按采购人通知及时进场，倒排安装调试计划，保障在约定时限内完成。",
            "5）安装调试的应急预案：对场地条件异常、配件缺失、电源环境不符、联机故障等情况设置应急处理机制。",
            "",
            "#### 3. 质量保证及技术措施",
            "1）质量保证管理体系：建立项目质量保证管理体系，明确项目经理总负责制。",
            "2）质量技术人员方案及职责分工：明确安装、调试、培训、售后岗位责任表与人员分工。",
            "3）监督机制：对发货、到货、安装、调试、验收等关键节点设置复核机制和责任追踪。",
            "4）质量问题应急处理方案：如发生质量问题，第一时间隔离问题设备/配件、分析原因并落实纠正和补救措施。",
            "",
            "#### 4. 售后服务方案",
            "1）售后服务流程：报修受理→远程诊断→现场服务→故障排除→回访闭环。",
            "2）售后服务标准：按厂家及行业规范提供维保、巡检、升级和备件保障服务。",
            "3）售后服务人员安排：明确售后负责人、工程师及联系电话。",
            "4）售后应急处理方案：对停机、核心部件异常等情况启动快速响应机制。",
            "",
            "#### 5. 采购文件原始售后/服务要求逐项承诺",
        ])

        if raw_service_points:
            for idx, point in enumerate(raw_service_points, start=1):
                parts.append(f"{idx}）{point}")
        else:
            parts.append("1）按采购文件售后服务要求执行。")

        parts.extend([
            "",
            "#### 6. 培训与验收配合措施",
            "1）对操作人员开展开关机、标准操作流程、注意事项、常见问题处理等培训。",
            "2）对管理人员开展设备管理、维护要求、风险控制、记录留存等培训。",
            "3）到货验收：配合采购人对外包装、数量、随机附件、资料进行验收。",
            "4）安装验收：提交安装调试记录，配合完成功能配置验收。",
            "5）技术验收：按招标文件技术参数逐项核验，并提供相应证明资料。",
            "",
        ])

    parts.extend([
        "供应商全称：【待填写：投标人名称】",
        "日期：【待填写：年 月 日】",
    ])
    return "\n".join(parts)
