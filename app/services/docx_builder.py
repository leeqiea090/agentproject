"""Word 文档生成服务（基于 python-docx）"""
import re
from pathlib import Path
from datetime import datetime
from typing import Iterable

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.shared import Cm, Pt
from app.schemas import BidDocumentSection, TenderDocument, CompanyProfile


def _set_cell_bg(cell, hex_color: str) -> None:
    """设置表格单元格背景色"""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def _style_table(table) -> None:
    """为表格应用基础样式"""
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER


def _add_heading(doc: Document, text: str, level: int) -> None:
    """添加标题（1~3级），并避免标题落在页末单独成页/孤行。"""
    heading = doc.add_heading(text, level=level)
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT

    run = heading.runs[0] if heading.runs else heading.add_run(text)
    run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
    run.font.name = "黑体"
    run.element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    run.font.bold = True

    if level == 1:
        run.font.size = Pt(16)
    elif level == 2:
        run.font.size = Pt(14)
    else:
        run.font.size = Pt(12)

    fmt = heading.paragraph_format
    fmt.keep_with_next = True      # 标题和下一段绑定，避免标题单独留页尾
    fmt.keep_together = True       # 标题自身不拆
    fmt.widow_control = True
    fmt.space_before = Pt(6)
    fmt.space_after = Pt(6)


def _add_paragraph(doc: Document, text: str, bold: bool = False) -> None:
    """添加普通段落"""
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.font.size = Pt(11)
    run.font.bold = bold
    para.paragraph_format.space_after = Pt(4)


def _append_inline_runs(para, text: str, size: Pt) -> None:
    """将含 **粗体** 的文本写入段落"""
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) >= 4:
            run = para.add_run(part[2:-2])
            run.bold = True
        else:
            run = para.add_run(part)
        run.font.size = size


def _normalize_title(text: str) -> str:
    """标题归一化（用于去重匹配）"""
    return re.sub(r"[\s#`*:：、（）()\-—_]", "", text or "")

def _normalize_cover_placeholder(value: str, label: str) -> str:
    text = (value or "").strip()
    if not text:
        return f"【待填写：{label}】"
    if text.startswith("[") and text.endswith("]"):
        return f"【待填写：{label}】"
    return text


def _clean_markdown_content(section_title: str, content: str) -> str:
    """
    清洗章节内容：
    1. 去除 Markdown 代码块围栏
    2. 去除与章节标题重复的首部标题
    """
    lines = content.splitlines()
    cleaned: list[str] = []
    norm_section = _normalize_title(section_title)
    can_skip_heading = True

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if stripped.startswith(">"):
            line = re.sub(r"^>\s*", "", stripped)
            stripped = line.strip()
        if stripped.startswith("#### "):
            line = "### " + stripped[5:]
            stripped = line.strip()

        if can_skip_heading and stripped:
            raw_heading = re.sub(r"^#+\s*", "", stripped)
            norm_heading = _normalize_title(raw_heading)
            if (
                norm_heading == norm_section
                or (norm_section and norm_heading.startswith(norm_section))
                or (norm_section and norm_section.startswith(norm_heading))
            ):
                continue
            # 只在章节开头阶段尝试跳过重复标题
            if stripped and not stripped.startswith("#"):
                can_skip_heading = False

        cleaned.append(line.rstrip())

    return "\n".join(cleaned).strip()


def _get_fixed_table_widths(header_cells: list[str]):
    key = tuple(header_cells)

    if key == ("序号", "服务名称", "磋商文件的服务需求", "响应文件响应情况", "偏离情况"):
        return [Cm(1.2), Cm(2.8), Cm(5.8), Cm(5.8), Cm(2.2)]

    if key == ("序号", "审查项", "招标文件要求", "响应情况", "对应材料/页码"):
        return [Cm(1.2), Cm(3.0), Cm(6.0), Cm(4.5), Cm(3.0)]

    return None

def _extract_outline_items(content: str) -> list[str]:
    """从章节 Markdown 中提取目录项（主要提取二级小节）"""
    items: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        candidate = ""
        if stripped.startswith("## "):
            candidate = stripped[3:].strip()
        elif re.match(r"^[一二三四五六七八九十]+、", stripped):
            candidate = stripped
        elif re.match(r"^[（(][一二三四五六七八九十]+[）)]", stripped):
            candidate = stripped

        if candidate and candidate not in seen:
            seen.add(candidate)
            items.append(candidate)

    return items[:20]


def _enable_update_fields_on_open(doc: Document) -> None:
    """要求 Word 打开文档时尝试更新域（含目录）。"""
    settings = doc.settings.element
    update_fields = settings.find(qn("w:updateFields"))
    if update_fields is None:
        update_fields = OxmlElement("w:updateFields")
        settings.append(update_fields)
    update_fields.set(qn("w:val"), "true")


def _insert_toc_field(paragraph, levels: str = "1-3") -> None:
    """
    在段落中插入 Word 目录域：
    { TOC \\o "1-3" \\h \\z \\u }

    \\o "1-3" = 收录 1~3 级标题
    \\h       = 目录项带超链接，可点击跳转
    \\z       = Web 布局中隐藏页码
    \\u       = 使用段落大纲级别
    """
    # begin
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    fld_begin.set(qn("w:dirty"), "true")
    run._r.append(fld_begin)

    # instrText
    run = paragraph.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f'TOC \\o "{levels}" \\h \\z \\u'
    run._r.append(instr)

    # separate
    run = paragraph.add_run()
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    run._r.append(fld_sep)

    # 占位显示文本（真正目录会在 Word 更新域后出现）
    hint_run = paragraph.add_run("目录将在打开文档后自动更新；如未更新，请在 Word 中右键目录并选择“更新域”，或按 F9。")
    hint_run.font.size = Pt(10.5)
    hint_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    # end
    run = paragraph.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_end)


def _add_toc(doc: Document, sections: Iterable[BidDocumentSection]) -> None:
    """添加可点击跳转的 Word 自动目录。"""
    _add_heading(doc, "目录", 1)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    _insert_toc_field(p, levels="1-3")

    tip = doc.add_paragraph()
    tip_run = tip.add_run("提示：目录项生成后可直接点击跳转到对应章节。")
    tip_run.font.size = Pt(9.5)
    tip_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

def _render_markdown_table(doc: Document, lines: list[str]) -> None:
    """将 Markdown 表格渲染为固定列数、固定列宽的 Word 表格"""
    data_rows = [l for l in lines if not re.match(r"^\|[-| :]+\|$", l.strip())]
    if not data_rows:
        return

    rows_cells = []
    for row in data_rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        rows_cells.append(cells)

    if not rows_cells:
        return

    # 关键：以表头列数为准，不再取 max(len(row))
    header_cells = rows_cells[0]
    col_count = len(header_cells)

    normalized_rows = [header_cells]
    for row in rows_cells[1:]:
        if len(row) < col_count:
            row = row + [""] * (col_count - len(row))
        elif len(row) > col_count:
            row = row[:col_count - 1] + [" | ".join(row[col_count - 1:])]
        normalized_rows.append(row)

    table = doc.add_table(rows=len(normalized_rows), cols=col_count)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _style_table(table)

    widths = _get_fixed_table_widths(header_cells)

    for i, row_data in enumerate(normalized_rows):
        for j, cell_text in enumerate(row_data):
            cell = table.cell(i, j)
            clean_text = cell_text.replace("**", "")
            cell.text = clean_text

            p = cell.paragraphs[0]
            run = p.runs[0] if p.runs else p.add_run(clean_text)
            run.font.size = Pt(10)

            if i == 0:
                run.font.bold = True
                _set_cell_bg(cell, "D9E1F2")

            if widths and j < len(widths):
                cell.width = widths[j]

    doc.add_paragraph()


def _parse_and_render_markdown(doc: Document, content: str) -> None:
    """
    将 Markdown 文本逐行解析并写入 Word 文档。
    支持：# 标题、**粗体**、- 列表、| 表格、普通段落
    """
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 空行
        if not stripped:
            i += 1
            continue

        # 代码块围栏
        if stripped.startswith("```"):
            i += 1
            continue

        # 标题
        if stripped.startswith(">"):
            stripped = re.sub(r"^>\s*", "", stripped)
        if stripped.startswith("### "):
            _add_heading(doc, stripped[4:], 3)
        elif stripped.startswith("#### "):
            _add_heading(doc, stripped[5:], 3)
        elif stripped.startswith("## "):
            _add_heading(doc, stripped[3:], 2)
        elif stripped.startswith("# "):
            _add_heading(doc, stripped[2:], 1)
        elif re.match(r"^第[一二三四五六七八九十]+章[、\s]", stripped):
            _add_heading(doc, stripped, 1)
        elif re.match(r"^[一二三四五六七八九十]+、", stripped):
            _add_heading(doc, stripped, 2)
        elif re.match(r"^[（(][一二三四五六七八九十]+[）)]", stripped):
            _add_heading(doc, stripped, 3)

        # 分割线
        elif stripped.startswith("---"):
            doc.add_paragraph("─" * 40)

        # 无序列表
        elif stripped.startswith("- ") or stripped.startswith("* "):
            para = doc.add_paragraph(style="List Bullet")
            _append_inline_runs(para, stripped[2:], Pt(10.5))

        # 有序列表
        elif re.match(r"^\d+[\.、]\s*", stripped):
            para = doc.add_paragraph(style="List Number")
            _append_inline_runs(para, re.sub(r"^\d+[\.、]\s*", "", stripped), Pt(10.5))

        # Markdown 表格：收集连续的表格行一起渲染
        elif stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            _render_markdown_table(doc, table_lines)
            continue

        # 普通段落（含粗体处理）
        else:
            para = doc.add_paragraph()
            _append_inline_runs(para, stripped, Pt(11))
            para.paragraph_format.space_after = Pt(4)

        i += 1


def _set_document_style(doc: Document) -> None:
    """设置文档全局样式"""
    style = doc.styles["Normal"]
    style.font.name = "仿宋"
    style.font.size = Pt(11)
    # 兼容中文字体
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋")

    # 页边距
    section = doc.sections[0]
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(2.5)


def _add_cover(doc: Document, tender: TenderDocument, company: CompanyProfile) -> None:
    """生成投标文件封面"""
    # 顶部大标题
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t_run = title.add_run("政 府 采 购 响 应 文 件")
    t_run.font.size = Pt(26)
    t_run.font.bold = True
    t_run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)
    t_run.font.name = "黑体"
    t_run.element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")

    doc.add_paragraph()
    doc.add_paragraph()

    def center_kv(key: str, value: str) -> None:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_key = p.add_run(f"{key}：")
        r_key.font.size = Pt(14)
        r_key.font.bold = True
        r_val = p.add_run(value)
        r_val.font.size = Pt(14)

    center_kv("项目名称", tender.project_name)
    center_kv("项目编号", tender.project_number)

    doc.add_paragraph()
    doc.add_paragraph()

    # 企业信息框
    table = doc.add_table(rows=4, cols=2)
    _style_table(table)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    info = [
        ("供应商全称", _normalize_cover_placeholder(company.name, "投标人名称")),
        ("法定代表人", _normalize_cover_placeholder(company.legal_representative, "法定代表人")),
        ("联系电话", _normalize_cover_placeholder(company.phone, "联系电话")),
        ("日期", datetime.now().strftime("%Y年%m月%d日")),
    ]
    for row_idx, (label, val) in enumerate(info):
        row = table.rows[row_idx]
        row.cells[0].text = label
        row.cells[1].text = val
        for cell in row.cells:
            for run in cell.paragraphs[0].runs:
                run.font.size = Pt(12)
        row.cells[0].paragraphs[0].runs[0].font.bold = True


def _assert_new_structure_only(sections, tender=None) -> None:
    titles = [getattr(s, "section_title", "") or "" for s in (sections or [])]
    text = "\n".join(titles) + " " + str(getattr(tender, "project_number", "") or "")

    is_tp = "[TP]" in text or "竞争性谈判" in text
    is_cs = "[CS]" in text or "竞争性磋商" in text

    if is_tp:
        required = {
            "一、响应文件封面格式",
            "二、报价书",
            "三、报价一览表",
            "四、资格承诺函",
            "五、技术偏离及详细配置明细表",
            "六、技术服务和售后服务的内容及措施",
            "七、资格性审查响应对照表",
            "八、符合性审查响应对照表",
            "九、投标无效情形汇总及自检表",
        }
        forbidden = {
            "一、封面格式",
            "二、首轮报价表",
            "三、分项报价表",
            "五、详细配置明细",
            "六、技术偏离表",
            "七、报价书附件",
            "六、法定代表人/单位负责人授权书",
        }
    else:
        required = {
            "一、响应文件封面格式",
            "二、首轮报价表",
            "三、分项报价表",
            "四、技术偏离及详细配置明细表",
            "五、技术服务和售后服务的内容及措施",
            "六、法定代表人/单位负责人授权书",
        }
        forbidden = {
            "一、封面格式",
            "二、报价书",
            "三、报价一览表",
            "四、资格承诺函",
            "五、详细配置明细",
            "六、技术偏离表",
            "七、报价书附件",
            "七、资格性审查响应对照表",
            "八、符合性审查响应对照表",
            "九、投标无效情形汇总及自检表",
        }

    missing = [x for x in required if x not in titles]
    hit = [x for x in titles if x in forbidden]

    if missing:
        raise RuntimeError(f"检测到当前采购方式对应必需章节缺失: {'；'.join(missing)}")
    if hit:
        raise RuntimeError(f"检测到当前采购方式不应出现的章节: {'；'.join(hit)}")

def build_bid_docx(
    sections: list[BidDocumentSection],
    tender: TenderDocument,
    company: CompanyProfile,
    output_path: Path,
) -> Path:
    """
    将投标文件各章节内容写入 Word (.docx) 文件。

    Args:
        sections: 各章节列表（BidDocumentSection）
        tender: 招标文件结构化数据
        company: 企业信息
        output_path: 输出 .docx 文件路径

    Returns:
        输出文件路径
    """
    _assert_new_structure_only(sections, tender=tender)
    doc = Document()
    _set_document_style(doc)
    _enable_update_fields_on_open(doc)

    # 封面
    #_add_cover(doc, tender, company)
    doc.add_page_break()

    # 逐章节渲染（跳过自动生成的封面/目录章节，避免重复）
    skip_titles = {"封面", "目录"}
    render_sections = [section for section in sections if section.section_title not in skip_titles]

    # 自动目录页
    if render_sections:
        _add_toc(doc, render_sections)
        doc.add_page_break()

    for idx, section in enumerate(render_sections):
        clean_content = _clean_markdown_content(section.section_title, section.content)

        # 章节标题
        _add_heading(doc, section.section_title, 1)
        # doc.add_paragraph()

        # 章节内容（Markdown → Word）
        _parse_and_render_markdown(doc, clean_content)

        # 附件列表
        if section.attachments:
            _add_paragraph(doc, "附件：", bold=True)
            for att in section.attachments:
                doc.add_paragraph(att, style="List Bullet")

        if idx < len(render_sections) - 1:
            doc.add_page_break()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path


