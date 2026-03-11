from __future__ import annotations

import app.services.one_click_generator.qualification_sections as _qualification_sections
import app.services.one_click_generator.technical_sections as _technical_sections

def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_qualification_sections, _technical_sections,):
    __reexport_all(_module)

del _module
