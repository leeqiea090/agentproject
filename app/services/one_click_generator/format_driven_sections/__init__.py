"""格式驱动章节生成入口。"""
from __future__ import annotations

from .cs import _build_cs_sections
from .tp import _build_tp_sections
from .zb import _build_zb_sections

def build_format_driven_sections(
    tender,
    tender_raw: str,
    products: dict | None = None,
    active_packages: list | None = None,
    *,
    normalized_result: dict | None = None,
    evidence_result: dict | None = None,
    product_profiles: dict | None = None,
) -> list:
    """
    对外统一导出函数：
    - TP 项目 -> _build_tp_sections
    - CS 项目 -> _build_cs_sections
    - ZB 项目 -> _build_zb_sections
    """
    mode = _detect_procurement_mode(tender, tender_raw)

    if mode == "tp":
        return _build_tp_sections(
            tender=tender,
            tender_raw=tender_raw,
            products=products,
            active_packages=active_packages,
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profiles=product_profiles,
        )

    if mode == "cs":
        return _build_cs_sections(
            tender=tender,
            tender_raw=tender_raw,
            products=products,
            active_packages=active_packages,
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profiles=product_profiles,
        )

    if mode == "zb":
        return _build_zb_sections(
            tender=tender,
            tender_raw=tender_raw,
            products=products,
            active_packages=active_packages,
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profiles=product_profiles,
        )

    # 未识别时：
    # 1）如果招标文件里已经提取到了“响应文件格式章节”，优先按 ZB 模板走
    # 2）否则再保守回退 TP
    if (getattr(tender, "response_section_titles", None) or getattr(tender, "response_section_templates", None)):
        return _build_zb_sections(
            tender=tender,
            tender_raw=tender_raw,
            products=products,
            active_packages=active_packages,
            normalized_result=normalized_result,
            evidence_result=evidence_result,
            product_profiles=product_profiles,
        )

    return _build_tp_sections(
        tender=tender,
        tender_raw=tender_raw,
        products=products,
        active_packages=active_packages,
        normalized_result=normalized_result,
        evidence_result=evidence_result,
        product_profiles=product_profiles,
    )


def _detect_procurement_mode(tender, tender_raw: str) -> str:
    text = " ".join(
        [
            str(getattr(tender, "project_name", "") or ""),
            str(getattr(tender, "project_number", "") or ""),
            str(getattr(tender, "procurement_type", "") or ""),
            " ".join(getattr(tender, "response_section_titles", []) or []),
            tender_raw or "",
        ]
    )

    if "[TP]" in text or "竞争性谈判文件" in text or "采购方式 竞争性谈判" in text or "竞争性谈判" in text:
        return "tp"

    if "[CS]" in text or "竞争性磋商文件" in text or "采购方式 竞争性磋商" in text or "竞争性磋商" in text:
        return "cs"

    if (
        "[ZB]" in text
        or "公开招标" in text
        or ("招标文件" in text and "投标人须知" in text and ("评标办法" in text or "综合评分法" in text))
    ):
        return "zb"

    return "unknown"
