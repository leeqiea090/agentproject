from __future__ import annotations

from app.schemas import (
    ClauseCategory,
    DocumentMode,
    NormalizedRequirement,
    WriterContext,
)

_TABLE_TYPE_CATEGORY_MAP: dict[str, list[ClauseCategory]] = {
    "technical_deviation": [ClauseCategory.technical_requirement],
    "config_list": [ClauseCategory.config_requirement],
    "service_response": [ClauseCategory.service_requirement, ClauseCategory.acceptance_requirement],
    "acceptance_doc_response": [ClauseCategory.documentation_requirement],
}


def build_writer_contexts(
    package_id: str,
    requirements: list[NormalizedRequirement],
    product_profile=None,
    tender_source_bindings: list | None = None,
    bid_evidence_bindings: list | None = None,
    document_mode: DocumentMode = DocumentMode.single_package_deep_draft,
) -> list[WriterContext]:
    """按包和分表类型构建 WriterContext。"""
    pkg_reqs = [r for r in requirements if r.package_id == package_id and r.category != ClauseCategory.noise]

    contexts: list[WriterContext] = []
    for table_type, categories in _TABLE_TYPE_CATEGORY_MAP.items():
        table_reqs = [r for r in pkg_reqs if r.category in categories]
        if not table_reqs:
            continue

        contexts.append(
            WriterContext(
                package_id=package_id,
                table_type=table_type,
                requirements=table_reqs,
                product_profile=product_profile,
                tender_source_bindings=[
                    b for b in (tender_source_bindings or []) if b.package_id == package_id
                ],
                bid_evidence_bindings=[
                    b for b in (bid_evidence_bindings or []) if b.package_id == package_id
                ],
                document_mode=document_mode,
            )
        )
    return contexts
