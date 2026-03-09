"""Word 文档生成服务（基于 python-docx）"""
import re
from pathlib import Path
from datetime import datetime

from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

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
    """添加标题（1~3级）"""
    heading = doc.add_heading(text, level=level)
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = heading.runs[0] if heading.runs else heading.add_run(text)
    run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)
    run.font.bold = True
    if level == 1:
        run.font.size = Pt(18)
    elif level == 2:
        run.font.size = Pt(14)
    else:
        run.font.size = Pt(12)


def _add_paragraph(doc: Document, text: str, bold: bool = False) -> None:
    """添加普通段落"""
    para = doc.add_paragraph()
    run = para.add_run(text)
    run.font.size = Pt(11)
    run.font.bold = bold
    para.paragraph_format.space_after = Pt(4)


def _render_markdown_table(doc: Document, lines: list[str]) -> None:
    """将 Markdown 表格渲染为 Word 表格"""
    # 过滤掉分隔行（|---|---|）
    data_rows = [l for l in lines if not re.match(r"^\|[-| :]+\|$", l.strip())]
    if not data_rows:
        return

    rows_cells = []
    for row in data_rows:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        rows_cells.append(cells)

    if not rows_cells:
        return

    col_count = max(len(r) for r in rows_cells)
    table = doc.add_table(rows=len(rows_cells), cols=col_count)
    _style_table(table)

    for i, row_data in enumerate(rows_cells):
        for j, cell_text in enumerate(row_data):
            if j >= col_count:
                break
            cell = table.cell(i, j)
            cell.text = cell_text
            run = cell.paragraphs[0].runs[0] if cell.paragraphs[0].runs else cell.paragraphs[0].add_run(cell_text)
            run.font.size = Pt(10)
            if i == 0:
                run.font.bold = True
                _set_cell_bg(cell, "D9E1F2")

    doc.add_paragraph()  # 表格后空行


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

        # 标题
        if stripped.startswith("### "):
            _add_heading(doc, stripped[4:], 3)
        elif stripped.startswith("## "):
            _add_heading(doc, stripped[3:], 2)
        elif stripped.startswith("# "):
            _add_heading(doc, stripped[2:], 1)

        # 分割线
        elif stripped.startswith("---"):
            doc.add_paragraph("─" * 40)

        # 无序列表
        elif stripped.startswith("- ") or stripped.startswith("* "):
            para = doc.add_paragraph(style="List Bullet")
            text = stripped[2:]
            # 处理粗体
            parts = re.split(r"(\*\*.*?\*\*)", text)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    run = para.add_run(part[2:-2])
                    run.bold = True
                else:
                    para.add_run(part)
            for run in para.runs:
                run.font.size = Pt(10.5)

        # 有序列表
        elif re.match(r"^\d+\. ", stripped):
            para = doc.add_paragraph(style="List Number")
            para.add_run(re.sub(r"^\d+\. ", "", stripped)).font.size = Pt(10.5)

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
            parts = re.split(r"(\*\*.*?\*\*)", stripped)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    run = para.add_run(part[2:-2])
                    run.bold = True
                else:
                    para.add_run(part)
            for run in para.runs:
                run.font.size = Pt(11)
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
        ("供应商全称", company.name),
        ("法定代表人", company.legal_representative),
        ("联系电话", company.phone),
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
    doc = Document()
    _set_document_style(doc)

    # 封面
    _add_cover(doc, tender, company)
    doc.add_page_break()

    # 逐章节渲染（跳过自动生成的封面/目录章节，避免重复）
    skip_titles = {"封面", "目录"}
    for section in sections:
        if section.section_title in skip_titles:
            continue

        # 章节标题
        _add_heading(doc, section.section_title, 1)
        doc.add_paragraph()

        # 章节内容（Markdown → Word）
        _parse_and_render_markdown(doc, section.content)

        # 附件列表
        if section.attachments:
            _add_paragraph(doc, "附件：", bold=True)
            for att in section.attachments:
                doc.add_paragraph(att, style="List Bullet")

        doc.add_page_break()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path
