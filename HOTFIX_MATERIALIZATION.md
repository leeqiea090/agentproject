# 紧急修复: TypeError 函数签名不匹配

## 问题描述

一键生成后台任务失败,报错:
```
TypeError: _build_materialized_service_section() got an unexpected keyword argument 'product_profiles'
```

## 根本原因

函数 `_build_materialized_service_section()` 的签名缺少 `product_profiles` 参数,但调用方在传递该参数。

**调用位置**: `app/services/tender_workflow/materialization.py:810`
```python
return _build_materialized_service_section(
    tender,
    products,
    product_profiles=product_profiles,  # ❌ 传入了参数
)
```

**原函数签名**: `app/services/tender_workflow/materialization.py:731`
```python
def _build_materialized_service_section(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
    # ❌ 缺少 product_profiles 参数
) -> str:
```

## 修复方案

在函数签名中添加 `product_profiles` 可选参数:

```python
def _build_materialized_service_section(
    tender: TenderDocument,
    products: dict[str, ProductSpecification],
    product_profiles: dict[str, Any] | None = None,  # ✅ 新增参数
) -> str:
    """构建materialized服务章节。"""
    parts: list[str] = []
    for pkg in _target_packages_for_materialization(tender, products):
        product = _product_for_package(pkg.package_id, products) or _fallback_single_product(products)
        # ✅ 优先使用传入的 product_profiles,否则回退到从 product 构建
        if product_profiles and pkg.package_id in product_profiles:
            profile = product_profiles[pkg.package_id]
        else:
            profile = _product_profile_for_materialization(product)
        # ... 后续代码
```

## 验证

```bash
python3 -c "
from app.services.tender_workflow.materialization import _build_materialized_service_section
import inspect
sig = inspect.signature(_build_materialized_service_section)
print('✅ 函数签名:', sig)
"
```

输出:
```
✅ 函数签名: (tender: 'TenderDocument', products: 'dict[str, ProductSpecification]', product_profiles: 'dict[str, Any] | None' = None) -> 'str'
```

## 影响范围

- **修改文件**: `app/services/tender_workflow/materialization.py`
- **修改行数**: 731-744 (14行)
- **影响功能**: 一键生成投标文件的后台任务
- **兼容性**: ✅ 向后兼容(新参数为可选参数,默认值为 None)

## 测试建议

1. 测试一键生成功能是否正常
2. 测试服务方案章节是否正确生成
3. 验证 product_profiles 传入时是否正确使用

---

**修复时间**: 2026-03-16
**状态**: ✅ 已修复
