from __future__ import annotations


def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value


from . import common as _common
from . import crud as _crud
from . import one_click as _one_click
from . import workflow as _workflow

for _module in (
    _common,
    _crud,
    _workflow,
    _one_click,
):
    __reexport_all(_module)

del _module

from .common import router

__all__ = ["router"]
