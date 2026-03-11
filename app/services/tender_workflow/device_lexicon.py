"""设备词典：按设备/包维护 forbidden 和 preferred 词集。

在需求归一化、证据绑定、hard gate 三处复用。
"""
from __future__ import annotations


DEVICE_LEXICON: dict[str, dict[str, set[str]]] = {
    "进口全自动电泳仪": {
        "forbidden": {"柯勒照明", "PMT", "流速模式", "激光器", "检测通道", "荧光补偿", "无限远校正光学系统"},
        "preferred": {"电泳", "凝胶", "染色", "电压", "温控", "电泳槽", "扫描"},
    },
    "进口荧光显微镜": {
        "forbidden": {"琼脂凝胶电泳", "电泳槽", "染色槽", "电泳系统", "特种蛋白"},
        "preferred": {"柯勒照明", "无限远校正", "荧光", "物镜", "目镜", "光源", "滤光片"},
    },
    "进口特种蛋白分析仪": {
        "forbidden": {"柯勒照明", "无限远校正光学系统", "进口荧光显微镜", "电泳槽", "PMT"},
        "preferred": {"特种蛋白", "免疫", "比浊", "散射", "蛋白", "试剂"},
    },
    "流式细胞仪": {
        "forbidden": {"电泳槽", "染色槽", "琼脂凝胶", "柯勒照明", "无限远校正"},
        "preferred": {"激光器", "荧光", "通道", "散射", "PMT", "鞘液", "上样", "补偿"},
    },
}


def get_forbidden_terms(device_name: str) -> set[str]:
    """根据设备名返回禁止词集合。支持模糊匹配。"""
    for key, lex in DEVICE_LEXICON.items():
        if key in device_name or device_name in key:
            return lex.get("forbidden", set())
    return set()


def get_preferred_terms(device_name: str) -> set[str]:
    """根据设备名返回优选词集合。支持模糊匹配。"""
    for key, lex in DEVICE_LEXICON.items():
        if key in device_name or device_name in key:
            return lex.get("preferred", set())
    return set()


def is_term_forbidden_for_device(term: str, device_name: str) -> bool:
    """检查某术语是否属于指定设备的禁止词。"""
    forbidden = get_forbidden_terms(device_name)
    return any(f in term for f in forbidden)
