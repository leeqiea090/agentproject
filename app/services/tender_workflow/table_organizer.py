"""分表组织器：将归一化需求按类别拆分为独立分表。

输出 5 类行集合：
- technical_rows    技术参数偏离表行
- config_rows       配置清单行
- service_rows      售后/培训/质保行
- acceptance_rows   验收要求行
- documentation_rows 资料/文档要求行

先组织表，再交给 writer 排版，人工后续好改很多。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas import ClauseCategory, NormalizedRequirement


@dataclass
class OrganizedTables:
    """分表组织结果。"""
    package_id: str = ""
    technical_rows: list[NormalizedRequirement] = field(default_factory=list)
    config_rows: list[NormalizedRequirement] = field(default_factory=list)
    service_rows: list[NormalizedRequirement] = field(default_factory=list)
    acceptance_rows: list[NormalizedRequirement] = field(default_factory=list)
    documentation_rows: list[NormalizedRequirement] = field(default_factory=list)
    noise_rows: list[NormalizedRequirement] = field(default_factory=list)
    commercial_rows: list[NormalizedRequirement] = field(default_factory=list)

    def total_valid_rows(self) -> int:
        return (
            len(self.technical_rows)
            + len(self.config_rows)
            + len(self.service_rows)
            + len(self.acceptance_rows)
            + len(self.documentation_rows)
            + len(self.commercial_rows)
        )

    def summary(self) -> dict[str, int]:
        return {
            "technical": len(self.technical_rows),
            "config": len(self.config_rows),
            "service": len(self.service_rows),
            "acceptance": len(self.acceptance_rows),
            "documentation": len(self.documentation_rows),
            "commercial": len(self.commercial_rows),
            "noise": len(self.noise_rows),
        }


_CATEGORY_MAP = {
    ClauseCategory.technical_requirement: "technical",
    ClauseCategory.config_requirement: "config",
    ClauseCategory.service_requirement: "service",
    ClauseCategory.acceptance_requirement: "acceptance",
    ClauseCategory.documentation_requirement: "documentation",
    ClauseCategory.commercial_requirement: "commercial",
    ClauseCategory.compliance_note: "technical",
    ClauseCategory.attachment_requirement: "documentation",
    ClauseCategory.noise: "noise",
}


def organize_requirements_into_tables(
    package_id: str,
    requirements: list[NormalizedRequirement],
) -> OrganizedTables:
    """将归一化需求按 ClauseCategory 分入对应分表。"""
    result = OrganizedTables(package_id=package_id)

    for req in requirements:
        bucket = _CATEGORY_MAP.get(req.category, "technical")
        getattr(result, f"{bucket}_rows").append(req)

    return result


def organize_all_packages(
    normalized_reqs: dict[str, list[NormalizedRequirement]],
) -> dict[str, OrganizedTables]:
    """对所有包执行分表组织。"""
    return {
        pkg_id: organize_requirements_into_tables(pkg_id, reqs)
        for pkg_id, reqs in normalized_reqs.items()
    }
