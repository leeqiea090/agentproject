from app.schemas import BidDocumentSection, BidGenerationPreferences
from app.services.bid_preferences import (
    apply_section_structure,
    format_section_titles,
    normalize_generation_preferences,
    reorder_bid_sections,
)


def test_normalize_generation_preferences_dedupes_section_order():
    prefs = normalize_generation_preferences(
        {
            "section_order": ["报价书", "技术服务和售后服务的内容及措施", "报价书", "  "],
            "custom_language_instruction": "  更正式一些  ",
        }
    )

    assert prefs is not None
    assert prefs.section_order == ["报价书", "技术服务和售后服务的内容及措施"]
    assert prefs.custom_language_instruction == "更正式一些"


def test_reorder_bid_sections_moves_matching_titles_first():
    sections = [
        BidDocumentSection(section_title="三、技术服务和售后服务的内容及措施", content="A"),
        BidDocumentSection(section_title="一、响应文件封面格式", content="B"),
        BidDocumentSection(section_title="二、报价书", content="C"),
    ]
    prefs = BidGenerationPreferences(
        section_order=[
            "报价书",
            "技术服务和售后服务的内容及措施",
        ]
    )

    ordered = reorder_bid_sections(sections, prefs)

    assert [section.section_title for section in ordered] == [
        "二、报价书",
        "三、技术服务和售后服务的内容及措施",
        "一、响应文件封面格式",
    ]


def test_format_section_titles_restarts_from_one_for_visible_sections():
    prefs = BidGenerationPreferences()

    titles = format_section_titles(
        ["一、响应文件封面格式", "二、报价书", "三、报价一览表"],
        prefs,
    )

    assert titles == [
        "响应文件封面格式",
        "一、报价书",
        "二、报价一览表",
    ]


def test_format_section_titles_supports_chapter_style():
    prefs = BidGenerationPreferences(section_numbering_style="chapter_cn")

    titles = format_section_titles(
        ["二、报价书", "三、技术服务和售后服务的内容及措施"],
        prefs,
    )

    assert titles == [
        "第一章 报价书",
        "第二章 技术服务和售后服务的内容及措施",
    ]


def test_apply_section_structure_merges_child_content_into_parent():
    sections = [
        BidDocumentSection(section_title="报价书", content="报价书正文"),
        BidDocumentSection(section_title="资格承诺函", content="资格承诺正文"),
    ]
    prefs = BidGenerationPreferences(
        section_structure=[
            {
                "section_title": "报价书",
                "children": [
                    {"section_title": "资格承诺函"},
                ],
            },
        ]
    )

    structured = apply_section_structure(sections, prefs)

    assert len(structured) == 1
    assert structured[0].section_title == "报价书"
    assert "报价书正文" in structured[0].content
    assert "## 资格承诺函" in structured[0].content
    assert "资格承诺正文" in structured[0].content
