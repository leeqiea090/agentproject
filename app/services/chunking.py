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


def split_to_blocks(
    text: str,
    chunk_size: int = 900,
    chunk_overlap: int = 150,
) -> list[DocumentBlock]:
    """将文本切分为可引用的 DocumentBlock 对象。

    """
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []

    if chunk_overlap >= chunk_size:
        chunk_overlap = max(0, chunk_size // 5)

    # ── 第一步：按包件/章节边界做硬切分 ──
    hard_breaks: list[int] = [0]
    for m in _PACKAGE_PATTERN.finditer(cleaned):
        pos = m.start()
        # 找到该行行首
        line_start = cleaned.rfind("\n", 0, pos)
        line_start = 0 if line_start == -1 else line_start + 1
        if line_start not in hard_breaks:
            hard_breaks.append(line_start)

    # 章节标题也做硬切分
    for m in re.finditer(r"\n(第[一二三四五六七八九十\d]+[章节])", cleaned):
        pos = m.start() + 1  # skip the \n
        if pos not in hard_breaks:
            hard_breaks.append(pos)

    hard_breaks.append(len(cleaned))
    hard_breaks.sort()

    # ── 第二步：在每个硬段内做软切分 ──
    blocks: list[DocumentBlock] = []
    current_table_id = ""
    current_table_header: list[str] = []
    table_row_idx = 0

    for seg_idx in range(len(hard_breaks) - 1):
        seg_start = hard_breaks[seg_idx]
        seg_end = hard_breaks[seg_idx + 1]
        segment = cleaned[seg_start:seg_end]

        if not segment.strip():
            continue

        # 检测该段的包件提示
        pkg_hint = _detect_package_hint(segment)

        # 在段内做 chunk 切分
        inner_start = 0
        inner_len = len(segment)

        while inner_start < inner_len:
            inner_end = min(inner_start + chunk_size, inner_len)
            chunk = segment[inner_start:inner_end]

            if inner_end < inner_len:
                boundary = max(chunk.rfind("\n\n"), chunk.rfind("\n"), chunk.rfind("。"))
                if boundary > int(chunk_size * 0.6):
                    inner_end = inner_start + boundary + 1
                    chunk = segment[inner_start:inner_end]

            chunk_text = chunk.strip()
            if not chunk_text:
                if inner_end >= inner_len:
                    break
                inner_start = max(inner_end - chunk_overlap, inner_start + 1)
                continue

            abs_start = seg_start + inner_start
            abs_end = seg_start + inner_end

            # 检测块属性
            first_line = chunk_text.split("\n", 1)[0]
            block_type = _detect_block_type(first_line)
            clause_no = _detect_clause_no(first_line)
            section_title = first_line if block_type == "header" else ""

            # 表格处理
            table_id = ""
            table_row = -1
            table_header: list[str] = []

            if block_type == "table_row":
                lines = chunk_text.split("\n")
                # 检查是否是新表格的开始（第一行是表头）
                if lines and _TABLE_ROW_PATTERN.match(lines[0].strip()):
                    cells = [c.strip() for c in lines[0].strip().strip("|").split("|")]
                    # 如果看起来像表头（不是分隔行）
                    if not _TABLE_SEP_PATTERN.match(lines[0].strip()):
                        if cells != current_table_header:
                            current_table_id = f"tbl_{abs_start}"
                            current_table_header = cells
                            table_row_idx = 0
                table_id = current_table_id
                table_header = current_table_header
                table_row = table_row_idx
                table_row_idx += 1

                # ── 逐格入库：每个单元格生成独立 DocumentBlock ──
                cell_row_idx = table_row
                for row_line in lines:
                    row_stripped = row_line.strip()
                    if not _TABLE_ROW_PATTERN.match(row_stripped):
                        continue
                    if _TABLE_SEP_PATTERN.match(row_stripped):
                        continue
                    cell_values = [c.strip() for c in row_stripped.strip("|").split("|")]
                    # 计算该行在原文中的精确偏移
                    row_offset_in_chunk = chunk.find(row_line)
                    row_abs_start = abs_start + (row_offset_in_chunk if row_offset_in_chunk >= 0 else 0)
                    cursor = 0
                    for col_idx, cell_text in enumerate(cell_values):
                        if not cell_text:
                            cursor += 1
                            continue
                        # 精确定位 cell 在行内的字符偏移
                        cell_pos_in_row = row_stripped.find(cell_text, cursor)
                        cell_char_start = row_abs_start + (cell_pos_in_row if cell_pos_in_row >= 0 else 0)
                        cell_char_end = cell_char_start + len(cell_text)
                        cursor = (cell_pos_in_row + len(cell_text)) if cell_pos_in_row >= 0 else cursor + len(cell_text)
                        col_header = table_header[col_idx] if col_idx < len(table_header) else ""
                        # 表头行和脚注/说明类单元格标记为 noise
                        cell_is_noise = bool(cell_row_idx == 0 and table_header) or _is_noise_cell(cell_text)
                        blocks.append(DocumentBlock(
                            text=cell_text,
                            package_hint=pkg_hint,
                            section_title=section_title,
                            clause_no=clause_no,
                            block_type="table_cell",
                            page=0,
                            char_start=cell_char_start,
                            char_end=cell_char_end,
                            table_id=table_id,
                            table_row=cell_row_idx,
                            table_col=col_idx,
                            table_header=[col_header] if col_header else [],
                            is_noise=cell_is_noise,
                        ))
                    cell_row_idx += 1
            else:
                # 非表格行重置表格状态
                current_table_id = ""
                current_table_header = []
                table_row_idx = 0

            # ── 说明行/表头/脚注单独标记，不进入主抽取链 ──
            inferred_type = block_type
            if block_type == "paragraph":
                stripped_lower = chunk_text.strip()
                if any(stripped_lower.startswith(p) for p in ("注：", "注:", "备注", "说明：", "说明:", "※")):
                    inferred_type = "footnote"
                elif any(stripped_lower.startswith(p) for p in ("详见", "见附件", "按规定")):
                    inferred_type = "description"

                # 噪音块判定：脚注/说明行/短表头行/表格表头行
            _is_noise = inferred_type in ("footnote", "description")
            if not _is_noise and block_type == "header" and len(chunk_text.strip()) < 6:
                _is_noise = True
            if not _is_noise and block_type == "table_row" and table_row == 0 and table_header:
                _is_noise = True

            blocks.append(DocumentBlock(
                text=chunk_text,
                package_hint=pkg_hint,
                section_title=section_title,
                clause_no=clause_no,
                block_type=inferred_type,
                page=0,
                char_start=abs_start,
                char_end=abs_end,
                table_id=table_id if block_type == "table_row" else "",
                table_row=table_row if block_type == "table_row" else -1,
                table_col=-1,
                table_header=table_header if block_type == "table_row" else [],
                is_noise=_is_noise,
            ))

            if inner_end >= inner_len:
                break
            inner_start = max(inner_end - chunk_overlap, inner_start + 1)

    return blocks
