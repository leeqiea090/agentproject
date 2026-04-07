import inspect

from app.services.one_click_generator import generate_bid_sections
from app.services.tender_workflow import _materialize_sections


def test_one_click_generator_exports_real_generate_function():
    params = inspect.signature(generate_bid_sections).parameters
    assert "tender" in params
    assert "tender_raw" in params
    assert "llm" in params


def test_tender_workflow_exports_real_materialize_function():
    params = inspect.signature(_materialize_sections).parameters
    assert "sections" in params
    assert "tender" in params
    assert "company" in params
