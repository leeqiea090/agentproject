from __future__ import annotations

from app.schemas import ProcurementPackage, ProductSpecification
from app.services.bid_generator import (
    _build_structured_technical_block,
    _ensure_compliance_branch_blocks,
    _sanitize_model_output,
)


def test_sanitize_model_output_removes_prompt_noise() -> None:
    content = (
        "你是投标文件专家。\n"
        "当然，以下是生成内容：\n"
        "System: 只允许输出JSON。\n"
        "## 二、正文\n"
        "本单位承诺按采购文件执行。\n"
    )
    cleaned = _sanitize_model_output("第二章 符合性承诺", content)
    assert "你是投标文件专家" not in cleaned
    assert "当然，以下是生成内容" not in cleaned
    assert "System: 只允许输出JSON" not in cleaned
    assert "本单位承诺按采购文件执行" in cleaned


def test_compliance_branch_blocks_are_always_present() -> None:
    source = "## 二、投标文件规范性、符合性承诺\n内容"
    ensured = _ensure_compliance_branch_blocks(source, allow_consortium=False, requires_sme=False)
    assert "联合体投标声明（分支选择）" in ensured
    assert "企业类型声明函（分支选择）" in ensured
    assert "分支D：非中小企业声明" in ensured
    assert "判定结果" not in ensured


def test_structured_technical_block_contains_evidence_mapping() -> None:
    package = ProcurementPackage(
        package_id="1",
        item_name="流式细胞分析仪",
        quantity=1,
        budget=100.0,
        technical_requirements={"激光器": "≥3", "荧光通道": "≥11"},
        delivery_time="30天",
        delivery_place="采购人指定地点",
    )
    product = ProductSpecification(
        product_name="流式细胞分析仪",
        manufacturer="某厂商",
        model="X100",
        origin="中国",
        specifications={"激光器": "3个", "荧光通道": "12个"},
        price=10.0,
    )
    block = _build_structured_technical_block(package, product)
    assert "技术条款证据映射表" not in block
    assert "证据映射完整性" not in block
    assert "参数待补充" not in block
    assert "承诺满足" not in block
    assert "3个" in block
    assert "12个" in block


def test_structured_technical_block_keeps_unverified_items_pending() -> None:
    package = ProcurementPackage(
        package_id="1",
        item_name="流式细胞分析仪",
        quantity=1,
        budget=100.0,
        technical_requirements={"激光器": "≥3", "荧光通道": "≥11"},
        delivery_time="30天",
        delivery_place="采购人指定地点",
    )
    product = ProductSpecification(
        product_name="流式细胞分析仪",
        manufacturer="某厂商",
        model="X100",
        origin="中国",
        specifications={"激光器": "3个"},
        price=10.0,
    )

    block = _build_structured_technical_block(package, product)

    assert "待核实（未匹配到已证实产品事实）" in block
    # 技术偏离表新增列：投标型号 + 页码 + 备注
    assert "| 2 | 荧光通道 | ≥11 | X100 | 待核实（未匹配到已证实产品事实） | 待核实 |" in block
