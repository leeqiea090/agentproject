from __future__ import annotations

from app.schemas import BidDocumentSection
from app.services.tender_workflow import _prepare_citations, _second_validation


def _section(title: str, content: str) -> BidDocumentSection:
    return BidDocumentSection(section_title=title, content=content, attachments=[])


def test_prepare_citations_normalizes_and_deduplicates() -> None:
    long_text = "A" * 400
    hits = [
        {
            "text": long_text,
            "score": 0.91234567,
            "metadata": {"source": "tender::demo", "chunk_index": "2"},
        },
        {
            "text": long_text,
            "score": 0.91234567,
            "metadata": {"source": "tender::demo", "chunk_index": "2"},
        },
    ]

    citations = _prepare_citations(hits, limit=5)
    assert len(citations) == 1
    assert citations[0]["source"] == "tender::demo"
    assert citations[0]["chunk_index"] == 2
    assert citations[0]["score"] == 0.912346
    assert citations[0]["quote"].endswith("...")


def test_second_validation_detects_missing_items_and_placeholders() -> None:
    analysis_result = {
        "required_materials": [
            "营业执照",
            "授权书",
            "报价书",
        ],
        "citations": [],
    }
    validation_result = {"overall_status": "需补充"}
    sections = [
        _section("第一章 资格性证明文件", "营业执照 [待填写]"),
        _section("第二章 符合性承诺", "授权书说明"),
        _section("第三章 商务及技术部分", "技术参数"),
    ]

    result = _second_validation(
        analysis_result=analysis_result,
        validation_result=validation_result,
        sections=sections,
        generation_result={"citations": []},
    )

    assert result["overall_status"] == "需修订"
    assert any(item["name"] == "占位符与留空项检查" and item["status"] == "需修订" for item in result["check_items"])
    assert any(item["name"] == "技术条款证据映射" and item["status"] == "需修订" for item in result["check_items"])
    assert any("检索引用" in issue for issue in result["issues"])
    assert any("分章节生成不完整" in issue for issue in result["issues"])


def test_second_validation_passes_when_content_and_citations_are_complete() -> None:
    analysis_result = {
        "required_materials": [
            "营业执照",
            "授权书",
            "报价书",
            "技术偏离表",
        ],
        "citations": [{"source": "tender::demo", "chunk_index": 1, "score": 0.8, "quote": "项目要求摘要"}],
    }
    validation_result = {"overall_status": "通过"}
    sections = [
        _section("第一章 资格性证明文件", "营业执照与授权书材料已附。"),
        _section("第二章 符合性承诺", "报价书承诺与商务条款响应。"),
        _section("第三章 商务及技术部分", "技术偏离表逐条响应。技术条款证据映射表已附。"),
        _section("第四章 报价书附件", "报价书明细与附件目录。"),
    ]

    result = _second_validation(
        analysis_result=analysis_result,
        validation_result=validation_result,
        sections=sections,
        generation_result={"citations": [{"source": "tender::demo", "chunk_index": 2, "score": 0.7, "quote": "评分标准摘录"}]},
    )

    assert result["overall_status"] == "通过"
    assert all(item["status"] == "通过" for item in result["check_items"])
    assert result["issues"] == []
