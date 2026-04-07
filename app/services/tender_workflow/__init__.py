from __future__ import annotations

from . import agent as _agent
from . import classification as _classification
from . import common as _common
from . import evidence as _evidence
from . import materialization as _materialization
from . import product_facts as _product_facts
from . import reporting as _reporting
from . import sanitization as _sanitization
from . import validation as _validation


def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value


for _module in (
    _common,
    _classification,
    _product_facts,
    _evidence,
    _materialization,
    _sanitization,
    _reporting,
    _validation,
    _agent,
):
    __reexport_all(_module)


del _module

_materialize_sections = _materialization._materialize_sections
_match_requirements_to_product_facts = _product_facts._match_requirements_to_product_facts
