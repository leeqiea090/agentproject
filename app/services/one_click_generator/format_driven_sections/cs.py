"""竞争性磋商格式驱动章节生成。"""
from __future__ import annotations

from .common import *  # noqa: F401,F403

_CS_APPENDIX_TITLES = [
    "附一、资格性审查响应对照表",
    "附二、符合性审查响应对照表",
    "附三、详细评审响应对照表",
    "附四、投标无效情形汇总及自检表",
]

_CS_TEMPLATE_PATTERNS: list[tuple[str, str]] = [
    ("一、响应文件封面格式", r"一\s*、\s*响应文件封面格式"),
    ("二、首轮报价表", r"二\s*、\s*首轮报价表"),
    ("三、分项报价表", r"三\s*、\s*分项报价表"),
    ("四、技术偏离及详细配置明细表", r"四\s*、\s*技术偏离及详细配置明细表"),
    ("五、技术服务和售后服务的内容及措施", r"五\s*、\s*技术服务和售后服务的内容及措施"),
    ("六、法定代表人/单位负责人授权书", r"六\s*、\s*法定代表人\s*/\s*单位负责人授权书"),
    ("七、法定代表人/单位负责人和授权代表身份证明", r"七\s*、\s*法定代表人\s*/\s*单位负责人和授权代表身份证明"),
    ("八、小微企业声明函", r"八\s*、\s*小微企业声明函"),
    ("九、残疾人福利性单位声明函", r"九\s*、\s*残疾人福利性单位声明函"),
    ("十、投标人关联单位的说明", r"十\s*、\s*投标人关联单位的说明"),
    ("十一、资格承诺函", r"十一\s*、\s*资格承诺函"),
]

_CS_COMPLIANCE_LABELS = [
    "投标报价",
    "投标文件规范性、符合性",
    "主要商务条款",
    "联合体投标",
    "技术部分实质性内容",
    "其他要求",
]

_CS_DETAILED_LABELS = [
    "技术参数",
    "供货保证措施及运输方案",
    "安装调试阶段方案",
    "质量保证及技术措施",
    "售后服务方案",
    "投标报价得分",
]


def _norm_header(text: str) -> str:
    return "".join(str(text or "").split())


def _tidy_extracted_text(text: str) -> str:
    value = _clean_text(text)
    if not value:
        return ""
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"(?<=[（《“])\s+", "", value)
    value = re.sub(r"\s+(?=[）》。；：，、“])", "", value)
    value = re.sub(r"(?<=[：，。；、“])\s+", "", value)
    return value.strip()


def _tpl_header_titles(tpl) -> list[str]:
    columns = list(getattr(tpl, "columns", None) or [])
    headers = [_clean_text(getattr(col, "title", "")) for col in columns]
    headers = [header for header in headers if header]
    return headers


def _select_cs_headers(tender, attr_name: str, fallback_headers: list[str]) -> list[str]:
    tpl = getattr(tender, attr_name, None)
    headers = _tpl_header_titles(tpl)
    if len(headers) >= 2:
        return headers
    return fallback_headers


def _render_cs_row(
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
    row: list[str] = []
    for header in headers:
        norm = _norm_header(header)
        if "序号" in norm:
            row.append(seq)
        elif any(token in norm for token in ("审查项", "审查内容", "评审项", "条款名称")):
            row.append(item_name)
        elif any(token in norm for token in ("采购文件要求", "招标文件要求", "磋商文件要求", "合格条件", "评分要求", "评审标准")):
            row.append(requirement)
        elif "无效情形" in norm:
            row.append(invalid_item)
        elif any(token in norm for token in ("响应文件对应内容", "投标文件内容", "响应内容")):
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
    text = text or ""
    text = re.sub(r"-\s*第\s*\d+\s*页\s*-", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _dedupe_consecutive_lines(lines: list[str]) -> list[str]:
    result: list[str] = []
    previous = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line == previous:
            continue
        result.append(line)
        previous = line
    return result


def _clean_template_block(block: str, title: str) -> str:
    body = re.sub(r"-\s*第\s*\d+\s*页\s*-", "", block or "")
    body = body.strip()
    if not body:
        return ""

    title_pat = next((pat for key, pat in _CS_TEMPLATE_PATTERNS if key == title), None)
    if title_pat:
        body = re.sub(rf"^\s*(?:{title_pat})\s*", "", body, count=1)
        body = re.sub(rf"^\s*(?:{title_pat})\s*", "", body, count=1)

    lines = _dedupe_consecutive_lines(body.splitlines())
    cleaned: list[str] = []
    for line in lines:
        compact = _clean_text(line)
        if not compact:
            continue
        if compact == _clean_text(title):
            continue
        if compact in {"第 37 页", "第 38 页", "第 39 页", "第 40 页", "第 41 页", "第 42 页"}:
            continue
        cleaned.append(line.strip())

    return "\n".join(cleaned).strip()


def _extract_cs_format_block(tender_raw: str) -> str:
    text = tender_raw or ""
    if not text:
        return ""

    chapter_pat = re.compile(r"(?:^|\n)\s*第六章(?:\s*第六章)?\s*响应文件格式(?:与要求)?", re.M)
    expected_markers = (
        "一、响应文件封面格式",
        "二、首轮报价表",
        "三、分项报价表",
        "四、技术偏离及详细配置明细表",
        "五、技术服务和售后服务的内容及措施",
    )

    best_start = None
    for match in chapter_pat.finditer(text):
        tail = text[match.start():]
        window = tail[:3000]
        if all(marker in window for marker in expected_markers[:3]) and any(marker in window for marker in expected_markers[3:]):
            best_start = match.start()
            break

    if best_start is None:
        return ""

    tail = text[best_start:]
    stop = re.search(r"(?:^|\n)\s*第[七八九十]\s*章", tail, re.M)
    return tail[:stop.start()].strip() if stop else tail.strip()


def _extract_cs_template_blocks(tender_raw: str) -> dict[str, str]:
    block = _extract_cs_format_block(tender_raw)
    if not block:
        return {}

    hits: list[tuple[str, int]] = []
    cursor = 0
    for title, pat in _CS_TEMPLATE_PATTERNS:
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
        cleaned = _clean_template_block(raw, title)
        if cleaned:
            result[title] = cleaned
    return result


def _build_cs_template_section(tender_raw: str, title: str, fallback: str) -> str:
    blocks = _extract_cs_template_blocks(tender_raw)
    return blocks.get(title, fallback).strip()


def _extract_cs_review_block(tender_raw: str, anchor_patterns: list[str], stop_patterns: list[str]) -> str:
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


def _extract_contract_package_block(block: str, package_id: str) -> str:
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


def _spaced_keyword_pattern(text: str) -> str:
    chars = [re.escape(ch) for ch in re.sub(r"\s+", "", text or "") if ch.strip()]
    return r"\s*".join(chars) if chars else ""


def _extract_named_segments(text: str, markers: list[str]) -> list[tuple[str, str]]:
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


def _extract_cs_qualification_rows(pkg, tender_raw: str) -> list[tuple[str, str]]:
    block = _extract_cs_review_block(
        tender_raw,
        anchor_patterns=[r"表一资格性审查表[:：]?\s*表一资格性审查表[:：]?", r"表一资格性审查表[:：]?"],
        stop_patterns=[r"表二符合性审查表[:：]?", r"表三详细评审表[:：]?", r"第六章\s*响应文件格式"],
    )
    pkg_block = _extract_contract_package_block(block, str(getattr(pkg, "package_id", "") or ""))
    if not pkg_block:
        return [
            ("符合《中华人民共和国政府采购法》第二十二条规定的条件", "提供黑龙江省政府采购供应商资格承诺函或等效证明材料"),
            ("不存在《中华人民共和国政府采购法实施条例》第十八条情形", "提供承诺函或等效证明材料"),
            ("未被列入失信被执行人、重大税收违法失信主体、政府采购严重违法失信行为记录名单", "提供承诺函或查询结果"),
            ("法定代表人授权书", "提供标准格式授权书并按要求签字、加盖公章（法定代表人参加投标的不提供）"),
            ("特定资格性要求", "按项目产品类别提交医疗器械经营许可/备案凭证/生产许可证/注册证；非医疗器械无需提供"),
        ]

    marker_pat = re.compile(r"(（[一二三四五六七八九十]+）|法定代表人授权书(?=\s+提供)|特定资格性要求)")
    hits = list(marker_pat.finditer(pkg_block))
    rows: list[tuple[str, str]] = []
    for idx, match in enumerate(hits):
        start = match.start()
        end = hits[idx + 1].start() if idx + 1 < len(hits) else len(pkg_block)
        segment = _tidy_extracted_text(pkg_block[start:end])
        label = _tidy_extracted_text(match.group(1))
        if not segment:
            continue
        if label in {"法定代表人授权书", "特定资格性要求"}:
            requirement = segment[len(label):].strip(" ：:") or segment
            rows.append((label, requirement))
            continue

        split_at = segment.rfind("提供")
        if split_at > 0:
            rows.append((segment[:split_at].strip(), segment[split_at:].strip()))
        else:
            rows.append((label, segment))

    return rows


def _build_cs_qualification_review_section(tender, packages, tender_raw: str) -> str:
    headers = _select_cs_headers(
        tender,
        "qualification_review_table",
        ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"],
    )
    parts: list[str] = []

    for pkg in packages:
        rows = [
            _render_cs_row(
                headers,
                seq=str(idx),
                item_name=item_name,
                requirement=requirement,
                response_placeholder=_suggest_cs_qualification_response(item_name, requirement),
            )
            for idx, (item_name, requirement) in enumerate(_extract_cs_qualification_rows(pkg, tender_raw), start=1)
        ]
        parts.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()


def _extract_cs_compliance_rows(pkg, tender_raw: str) -> list[tuple[str, str]]:
    block = _extract_cs_review_block(
        tender_raw,
        anchor_patterns=[r"表二符合性审查表[:：]?\s*表二符合性审查表[:：]?", r"表二符合性审查表[:：]?"],
        stop_patterns=[r"表三详细评审表[:：]?", r"第六章\s*响应文件格式"],
    )
    pkg_block = _extract_contract_package_block(block, str(getattr(pkg, "package_id", "") or ""))
    if not pkg_block:
        return _default_cs_compliance_rows()

    rows = []
    for label, segment in _extract_named_segments(pkg_block, _CS_COMPLIANCE_LABELS):
        requirement = segment[len(label):].strip(" ：:") or segment
        rows.append((label, requirement))
    return rows or _default_cs_compliance_rows()


def _build_cs_compliance_review_section(tender, packages, tender_raw: str) -> str:
    headers = _select_cs_headers(
        tender,
        "compliance_review_table",
        ["序号", "审查项", "采购文件要求", "响应文件对应内容", "是否满足", "备注"],
    )
    parts: list[str] = []

    for pkg in packages:
        rows = [
            _render_cs_row(
                headers,
                seq=str(idx),
                item_name=item_name,
                requirement=requirement,
                response_placeholder=_suggest_cs_compliance_response(item_name, requirement),
            )
            for idx, (item_name, requirement) in enumerate(_extract_cs_compliance_rows(pkg, tender_raw), start=1)
        ]
        parts.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()


def _extract_cs_detailed_rows(pkg, packages, tender_raw: str) -> list[tuple[str, str]]:
    block = _extract_cs_review_block(
        tender_raw,
        anchor_patterns=[r"表三详细评审表[:：]?\s*表三详细评审表[:：]?", r"表三详细评审表[:：]?"],
        stop_patterns=[r"第五章\s*主要合同条款", r"第六章\s*响应文件格式", r"第[五六七八九十]章"],
    )
    if not block:
        return []

    start_positions: list[tuple[str, int]] = []
    for candidate in packages:
        pat_text = _spaced_keyword_pattern(getattr(candidate, "item_name", ""))
        if not pat_text:
            continue
        match = re.search(pat_text, block)
        if match:
            start_positions.append((str(getattr(candidate, "package_id", "")), match.start()))

    target_pkg_id = str(getattr(pkg, "package_id", "") or "")
    pkg_start = next((pos for pkg_id, pos in start_positions if pkg_id == target_pkg_id), None)
    if pkg_start is None:
        pkg_block = block
    else:
        next_positions = sorted(pos for pkg_id, pos in start_positions if pos > pkg_start)
        end = next_positions[0] if next_positions else len(block)
        pkg_block = block[pkg_start:end]

    rows: list[tuple[str, str]] = []
    score_match = re.search(
        r"分值构成\s*技术部分\s*([0-9.]+)\s*分\s*商务部分\s*([0-9.]+)\s*分\s*报价得分\s*([0-9.]+)\s*分",
        pkg_block,
    )
    if score_match:
        rows.append(
            (
                "分值构成",
                f"技术部分 {score_match.group(1)} 分；商务部分 {score_match.group(2)} 分；报价得分 {score_match.group(3)} 分。",
            )
        )

    for label, segment in _extract_named_segments(pkg_block, _CS_DETAILED_LABELS):
        detail = re.sub(r"\s*(?:技术部分|商务部分|投标报价)\s*$", "", segment).strip()
        detail = re.sub(r"\s*）\s*评标委员会", " 评标委员会", detail)
        head_match = re.match(rf"{re.escape(label)}\s*(\([^)]+\))?", detail)
        review_item = _tidy_extracted_text((label + (" " + head_match.group(1) if head_match and head_match.group(1) else "")).strip())
        rows.append((review_item, _tidy_extracted_text(detail)))

    return rows


def _build_cs_detailed_review_section(tender, packages, tender_raw: str) -> str:
    headers = _select_cs_headers(
        tender,
        "detailed_review_table",
        ["序号", "评审项", "采购文件评分要求", "响应文件对应内容", "自评说明", "证明材料/页码"],
    )
    fallback_rows = [
        ("技术参数（20分）", "根据招标文件技术参数进行逐条评审，非★参数缺失达到规则阈值或重要配置功能缺失的按无效/废标条款处理。"),
        ("供货保证措施及运输方案（15分）", "按招标文件评分项分别覆盖供货流程、出库包装、运输应急、风险预防、交接验货五项。"),
        ("安装调试阶段方案（15分）", "按招标文件评分项分别覆盖人员配备、安装措施、调试措施、工期保障、应急预案五项。"),
        ("质量保证及技术措施（10分）", "按招标文件评分项分别覆盖质量体系、人员职责、监督机制、质量问题应急处理四项。"),
        ("售后服务方案（10分）", "按招标文件评分项分别覆盖售后方案、流程、标准、人员安排、应急处理五项。"),
        ("投标报价得分（30分）", "投标报价得分＝（评标基准价/投标报价）×价格分值。"),
    ]

    parts: list[str] = []
    for pkg in packages:
        parsed_rows = _extract_cs_detailed_rows(pkg, packages, tender_raw) or fallback_rows
        rows = [
            _render_cs_row(
                headers,
                seq=str(idx),
                item_name=item_name,
                requirement=rule,
                response_placeholder=_suggest_cs_detailed_response_location(item_name, rule),
                status_placeholder=_suggest_cs_detailed_response_note(item_name, rule),
                evidence_placeholder="【待填写：页码】",
            )
            for idx, (item_name, rule) in enumerate(parsed_rows, start=1)
        ]
        parts.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            _md_table(headers, rows),
            "",
        ])

    return "\n".join(parts).strip()


def _extract_cs_invalid_items(tender_raw: str) -> list[str]:
    text = _normalize_dense_text(tender_raw)
    if not text:
        return []

    blocks: list[str] = []
    patterns = [
        (
            r"响应文件存在下列任意一条的，则响应文件无效[:：]?",
            [r"6\.\s*供应商出现下列情况之一的，响应文件无效[:：]?", r"供应商出现下列情况之一的，响应文件无效[:：]?"],
        ),
        (
            r"供应商出现下列情况之一的，响应文件无效[:：]?",
            [r"7\.\s*供应商禁止行为", r"供应商禁止行为"],
        ),
    ]

    for anchor, stops in patterns:
        block = _extract_cs_review_block(text, [anchor], stops)
        if block:
            blocks.append(block)

    items: list[str] = []
    enum_pat = re.compile(r"（[一二三四五六七八九十]+）\s*(.*?)(?=（[一二三四五六七八九十]+）|$)")
    for block in blocks:
        for match in enum_pat.finditer(block):
            item = _tidy_extracted_text(match.group(1)).rstrip("；;。")
            if item:
                items.append(f"{item}。")

    extra_patterns = [
        r"资格性审查和符合性审查中凡有其中任意一项未通过.*?按无效投标处理。",
        r"投标报价经评审认定明显低于成本价.*?则对该供应商的响应文件作无效处理。",
        r"重要配置功能缺失的按废标处理。",
        r"不满足星号条款要求的按废标处理。",
    ]
    for pat in extra_patterns:
        for match in re.finditer(pat, text):
            items.append(_tidy_extracted_text(match.group(0)).rstrip("；;。") + "。")

    cleaned_items: list[str] = []
    seen: set[str] = set()
    for item in items:
        compact = _tidy_extracted_text(item)
        compact = re.split(r"注\s*[:：]", compact, maxsplit=1)[0].strip()
        if not compact or compact in seen:
            continue
        if any(
            token in compact for token in ("主要商务要求", "技术标准与要求", "附表一", "参数性质", "第 14 页")
        ):
            continue
        seen.add(compact)
        cleaned_items.append(compact)
    return cleaned_items


def _build_cs_invalid_bid_checklist(tender, tender_raw: str) -> str:
    headers = _select_cs_headers(tender, "invalid_bid_table", ["序号", "无效情形", "自检结果", "备注"])
    items = _extract_cs_invalid_items(tender_raw) or [
        "资格性审查和符合性审查中任意一项未通过的，按无效投标处理。",
        "任意一条不满足磋商文件★号条款要求的。",
        "单项产品五条及以上不满足非★号条款要求的。",
        "供应商所提报的技术参数未与竞争性磋商文件技术要求一一对应，且仅笼统填写“响应/完全响应”的。",
        "供应商提报的技术参数中没有明确品牌、型号、规格、配置等。",
        "单项商品报价超单项预算的。",
        "未按竞争性磋商文件规定要求签字、盖章的。",
        "响应文件中提供虚假材料的。",
        "提交的技术参数与所提供的技术证明文件不一致的。",
        "法定代表人/单位负责人授权书无法定代表人/单位负责人签字或没有加盖公章的。",
        "属于串通投标，或者依法被视为串通投标的。",
    ]
    rows = [
        _render_cs_row(headers, seq=str(idx), invalid_item=item, self_check_placeholder="【待填写：符合/不符合】")
        for idx, item in enumerate(items, start=1)
    ]
    return _md_table(headers, rows)


def _default_cs_compliance_rows() -> list[tuple[str, str]]:
    return [
        ("投标报价", "投标报价（包括分项报价，投标总报价）只能有一个有效报价且不超过采购预算或最高限价，投标报价不得缺项、漏项。"),
        ("投标文件规范性、符合性", "投标文件的签署、盖章、涂改、删除、插字、公章使用等符合招标文件要求；格式、文字、目录等符合招标文件要求或对投标无实质性影响。"),
        ("主要商务条款", "审查投标人出具的“满足主要商务条款的承诺书”，且进行签署或盖章。"),
        ("联合体投标", "符合关于联合体投标的相关规定。"),
        ("技术部分实质性内容", "明确所投产品品牌，并对招标文件提出的要求和条件作出明确响应，满足全部实质性要求。"),
        ("其他要求", "招标文件要求的其他无效投标情形；围标、串标和法律法规规定的其它无效投标条款。"),
    ]


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

    lines = [_clean_text(line) for line in block.splitlines() if _clean_text(line)]
    collecting = False
    merged: list[str] = []
    noise_patterns = (
        r"^(?:合同包\s*\d+|采购包\s*\d+|附表一[：:]?|参数性质|序号\s*要求|序号\s*具体技术|是否进口|核心产品|品目名称|标的名称|分项预算)",
        r"^(?:设备总台数|工业|详见附表一|我院设备的技术参数与性能要求的基本格式)$",
    )

    for line in lines:
        compact = line.strip()
        if "技术参数与性能要求" in compact:
            collecting = True
            continue
        if not collecting:
            continue
        if re.search(r"说明\s*打", compact):
            break
        if any(re.match(pattern, compact) for pattern in noise_patterns):
            continue

        if re.match(r"^(?:[※★*]?\d+(?:\.\d+)+|[※★*]?\d+)\s*", compact):
            merged.append(compact.lstrip("*").strip())
            continue

        if merged:
            merged[-1] += " " + compact

    rows: list[dict] = []
    seen_keys: set[str] = set()
    for item in merged:
        clean_item = _clean_text(item)
        if not clean_item or len(clean_item) < 4:
            continue
        row_key_match = re.match(r"^([※★]?\d+(?:\.\d+)*)", clean_item)
        row_key = row_key_match.group(1) if row_key_match else clean_item[:40]
        if row_key in seen_keys:
            continue
        seen_keys.add(row_key)
        rows.append(
            {
                "seq": str(len(rows) + 1),
                "item_name": pkg.item_name,
                "requirement": clean_item,
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

    lines = [_clean_text(line) for line in block.splitlines() if _clean_text(line)]
    collecting = False
    merged: list[str] = []
    for line in lines:
        compact = line.strip()
        if re.match(r"^(?:4\s*售后服务|\*?4\.1)", compact):
            collecting = True
        if not collecting:
            continue
        if re.match(r"^说明\s*打", compact):
            break
        if re.match(r"^(?:[※★*]?4\.\d+|4\s*售后服务)", compact):
            merged.append(compact.lstrip("*").strip())
            continue
        if merged:
            merged[-1] += " " + compact

    cleaned = []
    for item in merged:
        value = re.sub(r"-\s*第\s*\d+\s*页\s*-", "", item)
        value = re.sub(r"说明\s*打.*$", "", value)
        value = _tidy_extracted_text(value)
        if value and value != "4 售后服务":
            cleaned.append(value)
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
            "2）产品的出库、包装措施：发货前完成数量、型号、外观、随机附件复核；包装按原厂标准执行，落实防震、防潮、防压、防磕碰措施。",
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
            "1）售后服务方案：结合本包设备特点制定维保、巡检、备件和升级保障方案。",
            "2）售后服务流程：报修受理→远程诊断→现场服务→故障排除→回访闭环。",
            "3）售后服务标准：按厂家及行业规范提供维保、巡检、升级和备件保障服务。",
            "4）售后服务人员安排：明确售后负责人、工程师及联系电话。",
            "5）售后应急处理方案：对停机、核心部件异常等情况启动快速响应机制。",
            "",
            "#### 5. 采购文件原始售后要求逐项承诺",
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


def _suggest_cs_qualification_response(item_name: str, requirement: str = "") -> str:
    haystack = _tidy_extracted_text(f"{item_name} {requirement}")
    if any(token in haystack for token in ("中华人民共和国政府采购法》第二十二条", "资格承诺函")):
        return "十一、资格承诺函；营业执照或主体资格证明文件"
    if "法定代表人授权书" in haystack or "授权书" in haystack:
        return "六、法定代表人/单位负责人授权书；七、法定代表人/单位负责人和授权代表身份证明"
    if any(token in haystack for token in ("特定资格", "医疗器械", "注册证", "经营备案", "经营许可", "生产许可")):
        return "十一、资格承诺函后附医疗器械生产/经营许可、备案凭证、注册证等特定资格证明材料"
    if any(token in haystack for token in ("承诺通过合法渠道", "实施条例第十八条")):
        return "十一、资格承诺函"
    return "十一、资格承诺函及后附资格证明材料"


def _suggest_cs_compliance_response(item_name: str, requirement: str = "") -> str:
    haystack = _tidy_extracted_text(f"{item_name} {requirement}")
    if "投标报价" in haystack:
        return "二、首轮报价表（或电子投标客户端报价部分）；三、分项报价表"
    if any(token in haystack for token in ("规范性", "符合性", "签署", "盖章", "目录", "格式")):
        return "全册响应文件签字盖章页；六、法定代表人/单位负责人授权书"
    if any(token in haystack for token in ("主要商务条款", "商务条款")):
        return "五、技术服务和售后服务的内容及措施；六、法定代表人/单位负责人授权书"
    if "联合体投标" in haystack:
        return "二、首轮报价表或联合体协议（如适用）"
    if any(token in haystack for token in ("技术部分实质性内容", "品牌", "明确响应", "实质性要求")):
        return "四、技术偏离及详细配置明细表；五、技术服务和售后服务的内容及措施"
    if "其他要求" in haystack:
        return "十、投标人关联单位的说明；附四、投标无效情形汇总及自检表"
    return "对应章节及后附证明材料"


def _suggest_cs_detailed_response_location(item_name: str, rule: str = "") -> str:
    haystack = _tidy_extracted_text(f"{item_name} {rule}")
    if "分值构成" in haystack:
        return "附三、详细评审响应对照表（本表自评说明）"
    if any(token in haystack for token in ("技术参数", "技术要求", "配置功能")):
        return "四、技术偏离及详细配置明细表"
    if any(token in haystack for token in ("供货保证措施", "运输方案", "交接签收", "包装")):
        return "五、技术服务和售后服务的内容及措施 / 1.供货保证措施及运输方案"
    if any(token in haystack for token in ("安装调试", "工期保障", "应急预案")):
        return "五、技术服务和售后服务的内容及措施 / 2.安装调试阶段方案"
    if any(token in haystack for token in ("质量保证", "监督机制", "质量问题")):
        return "五、技术服务和售后服务的内容及措施 / 3.质量保证及技术措施"
    if any(token in haystack for token in ("售后服务", "响应时间", "维修支持")):
        return "五、技术服务和售后服务的内容及措施 / 4.售后服务方案 / 5.采购文件原始售后要求逐项承诺"
    if any(token in haystack for token in ("报价", "价格")):
        return "二、首轮报价表（或电子投标客户端报价部分）；三、分项报价表"
    return "【待填写：对应章节/材料】"


def _suggest_cs_detailed_response_note(item_name: str, rule: str = "") -> str:
    haystack = _tidy_extracted_text(f"{item_name} {rule}")
    if "分值构成" in haystack:
        return "按招标文件评分办法确认分值构成，本项一般无需单独举证。"
    if any(token in haystack for token in ("技术参数", "技术要求", "配置功能")):
        return "对照技术偏离表逐条填写品牌、型号、规格、配置及满足情况，不得仅写“响应”。"
    if any(token in haystack for token in ("供货保证措施", "运输方案", "交接签收", "包装")):
        return "围绕供货流程、包装措施、运输应急、风险预防、验货签收逐项说明满足情况。"
    if any(token in haystack for token in ("安装调试", "工期保障", "应急预案")):
        return "围绕人员配备、安装措施、调试措施、工期保障和应急预案逐项对应说明。"
    if any(token in haystack for token in ("质量保证", "监督机制", "质量问题")):
        return "围绕质量体系、岗位职责、监督机制、问题处置和验收配合逐项对应说明。"
    if any(token in haystack for token in ("售后服务", "响应时间", "维修支持")):
        return "结合质保期、响应时效、维修支持、售后流程、人员安排和应急处理逐项说明。"
    if any(token in haystack for token in ("报价", "价格")):
        return "按报价规则填写报价测算依据，并结合价格扣除政策说明得分依据。"
    return "【待填写：如何满足该评分项】"


def _build_cs_sections(
    tender,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list | None = None,
) -> list:
    _ = products
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

    sections.append(
        BidDocumentSection(
            section_title="四、技术偏离及详细配置明细表",
            content="\n\n".join(_build_cs_pkg_deviation_table(tender, pkg, tender_raw) for pkg in packages).strip(),
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
            section_title="七、法定代表人/单位负责人和授权代表身份证明",
            content=_build_cs_template_section(
                tender_raw,
                "七、法定代表人/单位负责人和授权代表身份证明",
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
            section_title="八、小微企业声明函",
            content=_build_cs_template_section(
                tender_raw,
                "八、小微企业声明函",
                "按招标文件原格式保留《中小企业声明函（货物）》；不适用时注明“本项不适用”。",
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="九、残疾人福利性单位声明函",
            content=_build_cs_template_section(
                tender_raw,
                "九、残疾人福利性单位声明函",
                "按招标文件原格式保留《残疾人福利性单位声明函》；不适用时注明“本项不适用”。",
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title="十、投标人关联单位的说明",
            content=_build_cs_template_section(
                tender_raw,
                "十、投标人关联单位的说明",
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
            section_title="十一、资格承诺函",
            content=_build_cs_template_section(
                tender_raw,
                "十一、资格承诺函",
                "按招标文件原格式保留《黑龙江省政府采购供应商资格承诺函》及社保缴纳证明材料清单。",
            ),
        )
    )

    sections.append(
        BidDocumentSection(
            section_title=_CS_APPENDIX_TITLES[0],
            content=_build_cs_qualification_review_section(tender, packages, tender_raw),
        )
    )
    sections.append(
        BidDocumentSection(
            section_title=_CS_APPENDIX_TITLES[1],
            content=_build_cs_compliance_review_section(tender, packages, tender_raw),
        )
    )
    sections.append(
        BidDocumentSection(
            section_title=_CS_APPENDIX_TITLES[2],
            content=_build_cs_detailed_review_section(tender, packages, tender_raw),
        )
    )
    sections.append(
        BidDocumentSection(
            section_title=_CS_APPENDIX_TITLES[3],
            content=_build_cs_invalid_bid_checklist(tender, tender_raw),
        )
    )

    return sections
