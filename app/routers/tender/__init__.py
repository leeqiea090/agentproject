from __future__ import annotations

from . import common as _common
from . import crud as _crud
from . import one_click as _one_click
from . import workflow as _workflow


def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value


for _module in (
    _common,
    _crud,
    _workflow,
    _one_click,
):
    __reexport_all(_module)


del _module


# 导出路由器对象
from .common import router