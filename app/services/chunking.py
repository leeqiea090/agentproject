from __future__ import annotations


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
            # Prefer natural boundaries to reduce semantic breaks.
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
