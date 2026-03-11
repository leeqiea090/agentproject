from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from app.schemas import DocumentBlock


# ── 包件边界检测模式 ──
_PACKAGE_PATTERN = re.compile(
    r"(包\s*(\d+)|第\s*(\d+)\s*包|采购包\s*(\d+)|Package\s*(\d+))",
    re.IGNORECASE,
)
# ── 章节标题模式 ──
_SECTION_HEADER_PATTERN = re.compile(
    r"^(#{1,4}\s+|第[一二三四五六七八九十\d]+[章节条款]|[一二三四五六七八九十]+[、.]|[\d]+[、.．]\s*)"
)
# ── 条款编号模式 ──
_CLAUSE_NO_PATTERN = re.compile(r"^(\d+(?:\.\d+)*)\s")
# ── 表格行模式（Markdown） ──
_TABLE_ROW_PATTERN = re.compile(r"^\|.*\|$")
_TABLE_SEP_PATTERN = re.compile(r"^\|[\s\-:|]+\|$")


def _detect_package_hint(text: str) -> str:
    """从文本中检测采购包提示。"""
    m = _PACKAGE_PATTERN.search(text[:200])
    if m:
        for g in m.groups()[1:]:
            if g:
                return g
    return ""


def _detect_clause_no(line: str) -> str:
    """从行首检测条款编号，如 '1.3' '2.1.4'。"""
    m = _CLAUSE_NO_PATTERN.match(line.strip())
    return m.group(1) if m else ""


def _detect_block_type(line: str) -> str:
    """检测行的块类型。"""
    stripped = line.strip()
    if _TABLE_ROW_PATTERN.match(stripped):
        return "table_row"
    if _SECTION_HEADER_PATTERN.match(stripped):
        return "header"
    if stripped.startswith(("-", "*", "•")) or re.match(r"^\d+[)）]", stripped):
        return "list_item"
    return "paragraph"


def _is_noise_cell(text: str) -> bool:
    """判断单元格内容是否为噪音（表头说明、脚注、纯序号等）。"""
    stripped = text.strip()
    if not stripped:
        return True
    # 纯序号
    if re.fullmatch(r"\d+", stripped):
        return True
    # 表头类关键词
    _NOISE_CELL_HINTS = ("序号", "编号", "项目", "备注", "说明", "注：", "注:", "※", "合计", "小计")
    if stripped in _NOISE_CELL_HINTS:
        return True
    if any(stripped.startswith(p) for p in ("注：", "注:", "备注", "说明：", "说明:", "※")):
        return True
    return False


def _is_noise_line(text: str) -> bool:
    """判断整行是否应作为噪音处理。"""
    stripped = text.strip()
    if not stripped:
        return True
    if any(stripped.startswith(prefix) for prefix in ("注：", "注:", "备注", "说明：", "说明:", "※")):
        return True
    if stripped in {"注", "说明", "备注"}:
        return True
    return False


def _cell_char_bounds(row_line: str, row_abs_start: int, cell_text: str, cursor: int) -> tuple[int, int, int]:
    """计算单元格在全文中的字符区间。"""
    if not cell_text:
        return row_abs_start + cursor, row_abs_start + cursor, cursor
    pos = row_line.find(cell_text, cursor)
    if pos < 0:
        pos = cursor
    start = row_abs_start + pos
    end = start + len(cell_text)
    return start, end, pos + len(cell_text)


def split_text(
    text: str,
    chunk_size: int = 900,
    chunk_overlap: int = 150,
) -> list[str]:
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []

    if chunk_overlap >= chunk_size:
        chunk_overlap = max(0, chunk_size // 5)

    chunks: list[str] = []
    start = 0
    total_length = len(cleaned)

    while start < total_length:
        end = min(start + chunk_size, total_length)
        chunk = cleaned[start:end]

        if end < total_length:
            boundary = max(chunk.rfind("\n\n"), chunk.rfind("\n"), chunk.rfind("。"))
            if boundary > int(chunk_size * 0.6):
                end = start + boundary + 1
                chunk = cleaned[start:end]

        chunk = chunk.strip()
        if chunk:
            chunks.append(chunk)

        if end >= total_length:
            break

        start = max(end - chunk_overlap, start + 1)

    return chunks


# ── 增强版：输出可引用 DocumentBlock ──


def _estimate_page(char_offset: int, chars_per_page: int = 1800) -> int:
    """根据字符偏移量估算页码（1-based）。"""
    if char_offset <= 0 or chars_per_page <= 0:
        return 1
    return (char_offset // chars_per_page) + 1


def split_to_blocks(
    text: str,
    chunk_size: int = 900,
    chunk_overlap: int = 150,
    chars_per_page: int = 1800,
) -> list[DocumentBlock]:
    """将文本切分为可引用的 DocumentBlock 对象。

    每个块携带 section_title / clause_no / page / table_id / row / col /
    char_start / char_end 等元数据；表头、脚注、说明行标记为 noise。
    """
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []

    blocks: list[DocumentBlock] = []
    current_package_id = ""
    current_section_title = ""
    current_clause_no = ""
    current_table_id = ""
    current_table_header: list[str] = []
    table_row_idx = 0
    offset = 0
    for raw_line in cleaned.splitlines(keepends=True):
        line = raw_line.rstrip("\n")
        stripped = line.strip()
        line_start = offset
        line_end = offset + len(line)
        offset += len(raw_line)

        if not stripped:
            current_table_id = ""
            current_table_header = []
            table_row_idx = 0
            continue

        detected_package_id = _detect_package_hint(stripped)
        if detected_package_id:
            current_package_id = detected_package_id
        package_hint = f"包{current_package_id}" if current_package_id else ""

        block_type = _detect_block_type(stripped)
        clause_no = _detect_clause_no(stripped)
        if clause_no:
            current_clause_no = clause_no
        if block_type == "header" and not clause_no:
            current_section_title = stripped

        if block_type == "header" and not clause_no:
            section_title = current_section_title or stripped
        else:
            section_title = current_section_title
        clause_for_block = clause_no or current_clause_no

        if block_type == "table_row":
            row_line = stripped
            if _TABLE_SEP_PATTERN.match(row_line):
                continue
            cell_values = [cell.strip() for cell in row_line.strip("|").split("|")]
            if not current_table_id:
                current_table_id = f"tbl_{line_start}"
                current_table_header = []
                table_row_idx = 0
            is_header_row = table_row_idx == 0
            if is_header_row:
                current_table_header = cell_values
            row_is_noise = is_header_row or any(_is_noise_cell(cell) for cell in cell_values) or _is_noise_line(stripped)
            cursor = 0
            for col_idx, cell_text in enumerate(cell_values):
                if not cell_text:
                    continue
                cell_start, cell_end, cursor = _cell_char_bounds(row_line, line_start, cell_text, cursor)
                col_header = ""
                if not is_header_row and col_idx < len(current_table_header):
                    col_header = current_table_header[col_idx]
                blocks.append(DocumentBlock(
                    text=cell_text,
                    package_id=current_package_id,
                    package_hint=package_hint,
                    section_title=section_title,
                    clause_no=clause_for_block,
                    block_type="table_cell",
                    page=_estimate_page(cell_start, chars_per_page),
                    char_start=cell_start,
                    char_end=cell_end,
                    table_id=current_table_id,
                    table_row=table_row_idx,
                    table_col=col_idx,
                    row=table_row_idx,
                    col=col_idx,
                    table_header=[col_header] if col_header else [],
                    is_noise=row_is_noise or _is_noise_cell(cell_text),
                ))
            table_row_idx += 1
            continue

        current_table_id = ""
        current_table_header = []
        table_row_idx = 0

        inferred_type = block_type
        if block_type == "paragraph":
            if _is_noise_line(stripped):
                inferred_type = "footnote"
            elif any(stripped.startswith(prefix) for prefix in ("详见", "见附件", "按规定")):
                inferred_type = "description"

        is_noise = inferred_type in {"footnote", "description"}
        if not is_noise and block_type == "header" and len(stripped) < 6:
            is_noise = True

        blocks.append(DocumentBlock(
            text=stripped,
            package_id=current_package_id,
            package_hint=package_hint,
            section_title=section_title,
            clause_no=clause_for_block,
            block_type=inferred_type,
            page=_estimate_page(line_start, chars_per_page),
            char_start=line_start,
            char_end=line_end,
            table_id="",
            table_row=-1,
            table_col=-1,
            row=-1,
            col=-1,
            table_header=[],
            is_noise=is_noise,
        ))

    return blocks
