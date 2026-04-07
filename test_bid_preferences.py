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
    assert "## 1. 资格承诺函" in structured[0].content
    assert "资格承诺正文" in structured[0].content


def test_apply_section_structure_strips_duplicate_child_heading():
    sections = [
        BidDocumentSection(section_title="报价书", content="报价书正文"),
        BidDocumentSection(section_title="资格承诺函", content="# 资格承诺函\n\n资格承诺正文"),
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

    assert structured[0].content.count("资格承诺函") == 1
    assert structured[0].content.count("## 1. 资格承诺函") == 1
    assert "\n# 资格承诺函\n" not in f"\n{structured[0].content}\n"


def test_apply_section_structure_supports_custom_sections_and_hidden_sections():
    sections = [
        BidDocumentSection(section_title="报价书", content="报价书正文"),
        BidDocumentSection(section_title="资格承诺函", content="资格承诺正文"),
    ]
    prefs = BidGenerationPreferences(
        section_structure=[
            {
                "section_title": "报价书",
                "custom_title": "商务响应",
            },
            {
                "section_title": "资格承诺函",
                "include": False,
            },
            {
                "section_title": "补充说明",
                "custom_title": "实施补充说明",
                "is_custom": True,
            },
        ]
    )

    structured = apply_section_structure(sections, prefs)

    assert [section.section_title for section in structured] == ["商务响应", "实施补充说明"]
    assert structured[0].content == "报价书正文"
    assert structured[1].content == "【待填写：本章节内容】"


def test_apply_section_structure_numbers_nested_sections_by_order():
    sections = [
        BidDocumentSection(section_title="报价书", content="报价书正文"),
        BidDocumentSection(section_title="资格承诺函", content="资格承诺正文"),
        BidDocumentSection(section_title="关联单位说明", content="关联单位正文"),
        BidDocumentSection(section_title="身份证明", content="身份证明正文"),
    ]
    prefs = BidGenerationPreferences(
        section_structure=[
            {
                "section_title": "报价书",
                "children": [
                    {
                        "section_title": "资格承诺函",
                        "children": [
                            {"section_title": "关联单位说明"},
                        ],
                    },
                    {"section_title": "身份证明"},
                ],
            },
        ]
    )

    structured = apply_section_structure(sections, prefs)

    assert "## 1. 资格承诺函" in structured[0].content
    assert "### 1.1 关联单位说明" in structured[0].content
    assert "## 2. 身份证明" in structured[0].content
