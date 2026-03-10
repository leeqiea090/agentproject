"""Quick test to verify all new implementations work correctly."""
import sys
sys.path.insert(0, ".")

# Test 1: one_click_generator imports and functions
from app.services.one_click_generator import (
    _atomize_requirement, _atomize_requirements,
    _DETAIL_TARGETS, _RICH_EXPANSION_MODE,
    _is_standard_config, _infer_config_usage, _infer_config_role,
)
print("=== one_click_generator imports OK ===")
print("DETAIL_TARGETS:", _DETAIL_TARGETS)
print("RICH_EXPANSION_MODE:", _RICH_EXPANSION_MODE)

# Test atomize with semicolons
result = _atomize_requirement("检测通道", "散射光通道>=3个；荧光通道>=10个；检测速度>=10000事件/秒")
print(f"\nAtomize (semicolons): {len(result)} items")
for r in result:
    print(f"  {r}")
assert len(result) == 3, f"Expected 3, got {len(result)}"

# Test generic summary filtering
result2 = _atomize_requirement("技术参数总括", "详见招标文件")
print(f"\nGeneric summary: {result2}")
assert len(result2) == 0, f"Generic summary should be filtered out, got {result2}"

# Test single requirement passthrough
result3 = _atomize_requirement("分辨率", ">=1920x1080")
print(f"\nSingle requirement: {result3}")
assert len(result3) == 1

# Test config helpers
print(f"\nStandard config (主机): {_is_standard_config('主机')}")
assert _is_standard_config("主机") is True
print(f"Standard config (选配模块): {_is_standard_config('选配模块')}")
assert _is_standard_config("选配模块") is False
print(f"Config usage (主机): {_infer_config_usage('血液分析仪主机')}")
print(f"Config role (软件, 设备): {_infer_config_role('分析软件', '流式细胞仪')}")

# Test 2: tender_workflow imports
from app.services.tender_workflow import (
    _expand_extracted_facts,
    _DETAIL_TARGETS as TW_DETAIL_TARGETS,
    _RICH_EXPANSION_MODE as TW_RICH,
    _build_product_profile_block,
    _detect_table_mode,
)
print("\n=== tender_workflow imports OK ===")
print("TW DETAIL_TARGETS:", TW_DETAIL_TARGETS)
print("TW RICH_EXPANSION_MODE:", TW_RICH)

# Test detect_table_mode for new 8-column format
cells_8col = ["条款编号", "招标要求", "投标型号", "实际响应值", "偏离情况", "证据材料", "页码", "说明/验收备注"]
mode = _detect_table_mode(cells_8col)
print(f"\n8-column mode detection: {mode}")
assert mode == "deviation", f"Expected 'deviation', got '{mode}'"

# Test old 5-column still works
cells_5col = ["序号", "招标技术参数要求", "投标产品响应参数", "偏离情况", "响应依据/证据映射"]
mode2 = _detect_table_mode(cells_5col)
print(f"5-column mode detection: {mode2}")
assert mode2 == "deviation", f"Expected 'deviation', got '{mode2}'"

# Test new config_detail mode
cells_cfg = ["序号", "配置名称", "单位", "数量", "是否标配", "用途说明", "备注"]
mode3 = _detect_table_mode(cells_cfg)
print(f"Config detail mode detection: {mode3}")
assert mode3 == "config_detail", f"Expected 'config_detail', got '{mode3}'"

# Test 3: router imports
from app.routers.tender import _expand_extracted_facts as route_expand
print("\n=== router imports OK ===")

# Test 4: Detail expander
from app.schemas import TenderDocument, ProcurementPackage, CommercialTerms, ProductSpecification

tender = TenderDocument(
    project_name="测试项目",
    project_number="TEST-001",
    purchaser="测试采购人",
    agency="测试代理机构",
    procurement_type="公开招标",
    budget=100000.0,
    packages=[
        ProcurementPackage(
            package_id="1",
            item_name="流式细胞仪",
            quantity=1,
            budget=100000.0,
            technical_requirements={"检测通道": ">=10个"},
        )
    ],
    commercial_terms=CommercialTerms(),
    evaluation_criteria={},
)

product = ProductSpecification(
    product_name="CytoFLEX",
    model="CytoFLEX-S",
    manufacturer="Beckman Coulter",
    specifications={"检测通道": "13个荧光通道"},
    price=80000.0,
)

normalized = {
    "technical_requirements": [
        {
            "requirement_id": "T-1-1",
            "package_id": "1",
            "parameter_name": "检测通道",
            "normalized_value": ">=10个",
        }
    ]
}

expansion = _expand_extracted_facts(normalized, {"1": product}, tender)
print(f"\nDetail expansion: {expansion['expanded_count']}/{expansion['total']} items")
print(f"Expansion rate: {expansion['expansion_rate']}")
for item in expansion["expanded_items"]:
    print(f"  {item['parameter_name']}: {item['detail_expansion'][:80]}...")

# Test 5: Product profile with writable description
product_fact_result = {
    "packages": [
        {
            "package_id": "1",
            "product_present": True,
            "product_profile_summary": {
                "product_name": "CytoFLEX",
                "manufacturer": "Beckman Coulter",
                "model": "CytoFLEX-S",
                "origin": "美国",
                "price": 80000.0,
                "registration_number": "国械注进20201234567",
                "certifications": ["CE", "FDA"],
                "specifications": {"检测通道": "13个荧光通道", "检测速度": "35000事件/秒"},
            },
            "offered_facts": [
                {"fact_name": "检测通道", "fact_value": "13个荧光通道"},
            ],
        }
    ]
}

profile = _build_product_profile_block(product_fact_result)
print(f"\nProduct profile block (length: {len(profile)} chars):")
has_description = "产品说明" in profile
has_evidence = "可引用技术事实" in profile
print(f"  Has writable description: {has_description}")
print(f"  Has evidence summary: {has_evidence}")
assert has_description, "Product profile should include writable description"
assert has_evidence, "Product profile should include evidence summary"

print("\n" + "=" * 60)
print("ALL TESTS PASSED!")
print("=" * 60)

