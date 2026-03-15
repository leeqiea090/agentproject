from __future__ import annotations

import app.services.one_click_generator.response_tables as _response_tables
import app.services.one_click_generator.config_tables as _config_tables

def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_response_tables, _config_tables,):
    __reexport_all(_module)

del _module
