from __future__ import annotations

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.table_builders as _table_builders

from app.schemas import ProcurementPackage, TenderDocument
from app.services.one_click_generator.common import (
    _fmt_money,
    _infer_package_quantity,
)

def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _table_builders,):
    __reexport_all(_module)

del _module
def _build_detail_quote_table(
    tender: TenderDocument,
    tender_raw: str,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    """构建明细报价表格。"""
    lines = [
        "| 序号 | 货物名称 | 规格型号 | 生产厂家 | 品牌 | 单价(元) | 数量 | 总价(元) |",
        "|---:|---|---|---|---|---:|---|---:|",
    ]

    pkgs = packages if packages is not None else tender.packages
    if not pkgs:
        lines.append("| 1 | 【待填写：货物名称】 | 【待填写：规格型号】 | 【待填写：生产厂家】 | 【待填写：品牌】 | 【待填写：单价】 | 【待填写：数量】 | 【待填写：总价】 |")
        lines.append("|  | **合计报价** |  |  |  |  |  | **【待填写：合计报价】** |")
        return "\n".join(lines)

    total_budget = 0.0
    for idx, pkg in enumerate(pkgs, start=1):
        total_budget += pkg.budget
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.append(
            f"| {idx} | {pkg.item_name} | 【待填写：品牌型号】 | 【待填写：生产厂家】 | 【待填写：品牌】 | 【待填写：单价】 | {quantity} | 【待填写：总价】 |"
        )

    lines.append(f"|  | **预算合计（参考）** |  |  |  |  |  | **{_fmt_money(total_budget)}** |")
    lines.append("|  | **投标总报价** |  |  |  |  |  | **【待填写：投标总报价】** |")
    table = "\n".join(lines)
    table += "\n\n> 填写规则：每行“总价(元)” = “单价(元)” × “数量”；底部“投标总报价”应与第三章《报价一览表》保持一致。"
    return table
