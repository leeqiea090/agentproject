from __future__ import annotations

from types import SimpleNamespace

from app.routers.tender import one_click
from app.schemas import BidDocumentSection, BidGenerationPreferences, ProcurementPackage, TenderDocument


class _Dumpable:
    def model_dump(self) -> dict:
        return {}


def _build_tender(procurement_type: str = "竞争性谈判") -> TenderDocument:
    return TenderDocument(
        project_name="示例项目",
        project_number="ABC-001",
        budget=0,
        purchaser="示例采购人",
        procurement_type=procurement_type,
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="示例设备",
                quantity=1,
                budget=0,
            )
        ],
    )


def test_build_interactive_preview_applies_structure_before_prompt_planning(monkeypatch):
    tender = _build_tender()
    generation_result = SimpleNamespace(
        validation_gate=_Dumpable(),
        regression_metrics=_Dumpable(),
        draft_level=SimpleNamespace(value="internal_draft"),
    )
    sections = [
        BidDocumentSection(section_title="报价书", content="报价书正文"),
        BidDocumentSection(section_title="资格承诺函", content="资格承诺正文"),
    ]
    observed: dict[str, object] = {}

    monkeypatch.setattr(one_click, "get_chat_model", lambda api_key=None: object())
    monkeypatch.setattr(
        one_click,
        "_generate_one_click_sections",
        lambda tender_doc, raw_text, selected_package, llm: (tender_doc, generation_result, sections),
    )
    monkeypatch.setattr(one_click, "render_editable_draft_sections", lambda sections, add_draft_watermark=False: sections)

    def _fake_plan_interactive_fill(current_sections, llm=None):
        observed["titles"] = [section.section_title for section in current_sections]
        observed["content"] = current_sections[0].content
        return {
            "sections": current_sections,
            "prompts": [],
            "manual_items": [],
        }

    monkeypatch.setattr(one_click, "plan_interactive_fill", _fake_plan_interactive_fill)

    preferences = BidGenerationPreferences(
        section_structure=[
            {
                "section_title": "报价书",
                "children": [
                    {"section_title": "资格承诺函"},
                ],
            },
        ]
    )

    preview = one_click._build_interactive_preview(
        tender_doc=tender,
        raw_text="示例原文",
        package_id="1",
        generation_preferences=preferences,
    )

    assert observed["titles"] == ["报价书"]
    assert "## 1. 资格承诺函" in str(observed["content"])
    assert preview["section_titles"] == ["报价书"]


def test_ensure_session_preview_cache_is_scoped_by_generation_preferences(monkeypatch):
    tender = _build_tender()
    session_data = {
        "tender_doc": tender.model_dump(),
        "raw_text": "示例原文",
        "package_cache": {},
    }
    calls: list[list[str]] = []

    def _fake_build_preview(
        tender_doc,
        raw_text,
        package_id,
        api_key=None,
        generation_preferences=None,
    ):
        prefs = generation_preferences or BidGenerationPreferences()
        calls.append(list(prefs.section_order))
        return {
            "package_id": package_id,
            "sections": [],
            "prompts": [],
            "draft_level": "internal_draft",
        }

    monkeypatch.setattr(one_click, "_build_interactive_preview", _fake_build_preview)

    prefs_a = BidGenerationPreferences(section_order=["报价书"])
    prefs_b = BidGenerationPreferences(section_order=["资格承诺函"])

    one_click._ensure_session_preview(session_data, "1", generation_preferences=prefs_a)
    one_click._ensure_session_preview(session_data, "1", generation_preferences=prefs_a)
    one_click._ensure_session_preview(session_data, "1", generation_preferences=prefs_b)

    assert calls == [["报价书"], ["资格承诺函"]]
    assert len(session_data["package_cache"]) == 2
