from __future__ import annotations

from . import common as _common
from . import pipeline as _pipeline
from . import sections as _sections
from . import table_builders as _table_builders
from . import writer_contexts as _writer_contexts
from app.services import evidence_binder as _evidence_binder
from app.services import quality_gate as _quality_gate
from app.services import requirement_processor as _requirement_processor


def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value


for _module in (
    _common,
    _requirement_processor,
    _evidence_binder,
    _quality_gate,
    _writer_contexts,
    _table_builders,
    _sections,
    _pipeline,
):
    __reexport_all(_module)


del _module


def generate_bid_sections():
    return None