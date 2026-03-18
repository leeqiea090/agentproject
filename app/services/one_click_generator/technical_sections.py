from __future__ import annotations

import re
from typing import Any

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.table_builders as _table_builders
import app.services.one_click_generator.qualification_sections as _qualification_sections
import app.services.evidence_binder as _evidence_binder
import app.services.quality_gate as _quality_gate
import app.services.requirement_processor as _requirement_processor

from app.schemas import (
    BidDocumentSection,
    ClauseCategory,
    NormalizedRequirement,
    ProcurementPackage,
    TenderDocument,
)
from app.services.one_click_generator.common import (
    _as_text,
    _clean_delivery_text,
    _safe_text,
)
from app.services.requirement_processor import (
    _markdown_cell,
)

def __reexport_all(module) -> None:
    """将指定模块的公开成员重新导出到当前命名空间。"""
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _table_builders, _qualification_sections, _evidence_binder, _quality_gate, _requirement_processor,):
    __reexport_all(_module)

del _module
def _rich_pending(message: str) -> str:
    """生成富底稿场景下的待补占位文本。"""
    return f"【待补：{message}】"


def _resolve_rich_commitment_response(
    requirement: NormalizedRequirement,
    *,
    warranty: str = "",
    evidence_refs: list[Any] | None = None,
) -> str:
    """解析并返回富承诺响应，针对不同售后条款生成细化承诺。"""
    param_name = _safe_text(requirement.param_name, "")
    raw_text = _safe_text(requirement.raw_text, "")
    text = f"{param_name} {raw_text}"
    evidence_refs = evidence_refs or []

    # 质保期条款
    if any(k in text for k in ("质保期", "保修期", "质量保证期")):
        if warranty:
            return f"{warranty}，自验收合格之日起计算"
        return "按采购文件要求执行，自验收合格之日起计算"

    # 响应时限条款
    if any(k in text for k in ("响应", "到场", "小时内", "分钟内", "响应时间", "到达时间")):
        # 提取时限
        import re
        time_match = re.search(r"(\d+)\s*(小时|分钟|h|min)", raw_text)
        if time_match:
            time_value = time_match.group(1)
            time_unit = time_match.group(2)
            unit_map = {"h": "小时", "min": "分钟"}
            time_unit = unit_map.get(time_unit, time_unit)
            return f"承诺{time_value}{time_unit}内响应，提供售后服务热线及联系人信息"
        return "承诺按采购文件要求时限响应，并提供7×24小时服务热线"

    # 维护保养条款
    if any(k in text for k in ("维护", "保养", "巡检", "定期维护", "定期保养")):
        freq_match = re.search(r"(\d+)\s*次[/／](年|季度|月)", raw_text)
        if freq_match:
            freq_value = freq_match.group(1)
            freq_unit = freq_match.group(2)
            return f"承诺质保期内至少{freq_value}次/{freq_unit}定期维护保养，并提供维护记录"
        return "承诺提供定期维护保养服务，并提供维护记录及使用指导"

    # 培训条款
    if any(k in text for k in ("培训", "操作培训", "使用培训", "维修培训")):
        return "承诺设备安装调试后对采购人操作人员进行现场培训，培训结束后提供培训记录及操作手册"

    # 备用机条款
    if any(k in text for k in ("备用机", "替代设备", "备机")):
        return "承诺如设备无法及时修复或维修周期超过约定时限，按采购文件要求提供备用机保障使用"

    # 备件/耗材条款
    if any(k in text for k in ("备件", "配件", "零部件", "耗材", "试剂")):
        return "承诺具备备件、耗材储备能力，确保关键零部件及耗材供应及时"

    # 升级服务条款
    if any(k in text for k in ("升级", "免费升级", "软件升级", "系统升级")):
        return "承诺质保期内提供软件或系统免费升级服务"

    # LIS/接口费用条款
    if any(k in text for k in ("LIS", "双向传输", "接口", "对接费用")):
        return "承诺承担双向LIS接口对接费用，并提供技术支持确保数据传输正常"

    # 维修密码/权限条款
    if any(k in text for k in ("维修密码", "密码", "权限", "维修权限")):
        return "承诺提供维修所需密码或必要权限支持，不设置不合理限制"

    # 维修资料条款
    if any(k in text for k in ("维修资料", "维护资料", "技术资料", "维修手册")):
        return "承诺提供维修所需技术资料、维护手册及故障排查指导"

    # 安装调试条款
    if any(k in text for k in ("安装", "调试", "安装调试", "到货安装")):
        return "承诺负责设备安装调试，直至设备达到正常使用状态，并提供安装调试记录"

    # 通用售后服务条款
    if evidence_refs:
        return f"详见{_as_text(evidence_refs[0])}"

    return "承诺按采购文件及厂家服务方案执行"





def _generate_rich_draft_sections(
    tender: TenderDocument,
    products: dict,
    *,
    draft_mode: str = "rich",
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    active_packages: list[ProcurementPackage] | None = None,
    writer_contexts: dict[str, list] | None = None,
) -> list[BidDocumentSection]:
    """双模式 Section Writer — 底稿优化版，固定分表输出。

    writer 输入必须是 requirement + package_context + table_type。
    固定分表输出：
    1. 技术参数响应表（仅 technical_requirement）
    2. 配置清单（仅 config_requirement）
    3. 售后服务响应表（仅 service_requirement）
    4. 验收/资料要求响应表（acceptance + documentation）

    每个包独立生成，只消费同包对象。
    当传入 writer_contexts 时，优先使用其中已校验的需求分组。
    """
    sections: list[BidDocumentSection] = []
    is_rich = draft_mode == "rich"
    normalized_reqs = normalized_reqs or {}
    writer_contexts = writer_contexts or {}
    packages = active_packages or tender.packages
    for pkg in packages:
        product = products.get(pkg.package_id)
        if not product:
            continue

        p_name = _as_text(getattr(product, "product_name", ""))
        p_model = _as_text(getattr(product, "model", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        p_brand = _as_text(getattr(product, "brand", ""))
        specs = getattr(product, "specifications", None) or {}
        config_items = getattr(product, "config_items", None) or []
        evidence_refs = getattr(product, "evidence_refs", None) or []
        has_real_product = bool(p_model and p_mfr)
        pkg_reqs = normalized_reqs.get(pkg.package_id, [])

        # 优先使用 WriterContext 中已校验的需求分组（保证包一致性）
        pkg_wctxs = writer_contexts.get(pkg.package_id, [])
        wctx_by_type = {wc.table_type: wc.requirements for wc in pkg_wctxs} if pkg_wctxs else {}

        # ── 分表1: 技术参数响应表 ──
        tech_reqs = wctx_by_type.get("technical_deviation") or [
            r for r in pkg_reqs if r.category == ClauseCategory.technical_requirement
        ]
        tech_content = f"### 包{pkg.package_id} 技术参数响应表\n\n"
        if has_real_product:
            tech_content += f"投标产品：**{p_brand or p_mfr} {p_name}**（型号：{p_model}）\n\n"
        if tech_reqs:
            tech_content += "| 序号 | 参数名称 | 招标要求 | 投标响应 | 偏离说明 |\n"
            tech_content += "|------|----------|----------|----------|----------|\n"
            for idx, req in enumerate(tech_reqs, 1):
                response = specs.get(req.param_name, "待核实（需填入投标产品实参）")
                deviation = "无偏离" if req.param_name in specs else "待核实"
                material_mark = "★" if req.is_material else ""
                tech_content += f"| {idx} | {material_mark}{_markdown_cell(req.param_name)} | {_markdown_cell(req.threshold or req.raw_text)} | {_markdown_cell(str(response))} | {deviation} |\n"
        else:
            tech_content += f"{_rich_pending('根据采购文件逐条补录技术参数响应表')}\n"
        tech_content += "\n"

        # ── 分表2: 配置清单 ──
        config_reqs = wctx_by_type.get("config_list") or [
            r for r in pkg_reqs if r.category == ClauseCategory.config_requirement
        ]
        config_content = f"### 包{pkg.package_id} 配置清单\n\n"
        if config_items:
            config_content += "| 序号 | 配置项 | 数量 | 说明 |\n"
            config_content += "|------|--------|------|------|\n"
            for idx, item in enumerate(config_items, 1):
                if isinstance(item, dict):
                    name = _as_text(item.get("配置项") or item.get("name", ""))
                    qty = _as_text(item.get("数量") or item.get("qty", "1"))
                    desc = _as_text(item.get("说明") or item.get("description", "标配"))
                    config_content += f"| {idx} | {_markdown_cell(name)} | {qty} | {_markdown_cell(desc)} |\n"
        elif config_reqs:
            config_content += "| 序号 | 配置项 | 招标要求 | 投标响应 |\n"
            config_content += "|------|--------|----------|----------|\n"
            for idx, req in enumerate(config_reqs, 1):
                config_content += (
                    f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | "
                    f"{_markdown_cell(_rich_pending('按投标配置清单填写'))} |\n"
                )
        else:
            config_content += f"{_rich_pending('按投标产品配置清单逐项填写')}\n"
        config_content += "\n"

        # ── 分表3: 售后服务响应表（按类别细化展示）──
        service_reqs = wctx_by_type.get("service_response") or [
            r for r in pkg_reqs if r.category == ClauseCategory.service_requirement
        ]
        service_content = f"### 包{pkg.package_id} 售后服务响应表\n\n"
        warranty = tender.commercial_terms.warranty_period
        service_content += f"**质保期**：{warranty or _rich_pending('按采购文件或厂家承诺填写')}\n\n"

        if service_reqs:
            # 按售后类别分组：响应/安装/培训/维保/升级/配件/其他
            categorized_reqs = {
                "响应时限": [],
                "安装调试": [],
                "培训服务": [],
                "维护保养": [],
                "升级服务": [],
                "配件耗材": [],
                "其他服务": [],
            }

            for req in service_reqs:
                text = f"{_safe_text(req.param_name, '')} {_safe_text(req.raw_text, '')}"
                if any(k in text for k in ("响应", "到场", "小时内", "分钟内")):
                    categorized_reqs["响应时限"].append(req)
                elif any(k in text for k in ("安装", "调试", "到货安装")):
                    categorized_reqs["安装调试"].append(req)
                elif any(k in text for k in ("培训", "操作培训", "使用培训")):
                    categorized_reqs["培训服务"].append(req)
                elif any(k in text for k in ("维护", "保养", "巡检", "定期维护")):
                    categorized_reqs["维护保养"].append(req)
                elif any(k in text for k in ("升级", "免费升级", "软件升级")):
                    categorized_reqs["升级服务"].append(req)
                elif any(k in text for k in ("备件", "配件", "耗材", "试剂")):
                    categorized_reqs["配件耗材"].append(req)
                else:
                    categorized_reqs["其他服务"].append(req)

            # 按类别输出表格
            idx = 1
            for category, reqs in categorized_reqs.items():
                if not reqs:
                    continue
                service_content += f"**{category}**\n\n"
                service_content += "| 序号 | 服务项 | 招标要求 | 投标承诺 |\n"
                service_content += "|------|--------|----------|----------|\n"
                for req in reqs:
                    response = _resolve_rich_commitment_response(
                        req,
                        warranty=_as_text(warranty),
                        evidence_refs=evidence_refs,
                    )
                    service_content += (
                        f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | "
                        f"{_markdown_cell(response)} |\n"
                    )
                    idx += 1
                service_content += "\n"
        else:
            service_content += f"{_rich_pending('按采购文件逐条补录售后服务承诺')}\n"

        # ── 分表4: 验收/资料要求响应表 ──
        accept_reqs = wctx_by_type.get("acceptance_doc_response") or [
            r for r in pkg_reqs if r.category in (
                ClauseCategory.acceptance_requirement,
                ClauseCategory.documentation_requirement,
            )
        ]
        accept_content = f"### 包{pkg.package_id} 验收及资料要求响应表\n\n"
        if accept_reqs:
            accept_content += "| 序号 | 类型 | 要求项 | 招标要求 | 投标响应 |\n"
            accept_content += "|------|------|--------|----------|----------|\n"
            for idx, req in enumerate(accept_reqs, 1):
                cat_label = "验收" if req.category == ClauseCategory.acceptance_requirement else "资料"
                accept_content += (
                    f"| {idx} | {cat_label} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | "
                    f"{_markdown_cell(_resolve_rich_commitment_response(req, evidence_refs=evidence_refs))} |\n"
                )
        else:
            accept_content += f"- 验收标准：{_rich_pending('按采购文件或验收方案填写')}\n"
            accept_content += f"- 随机文件：{_rich_pending('补充随机文件清单')}\n"
        accept_content += "\n"

        # ── 合并为一个章节 ──
        combined = tech_content + config_content + service_content + accept_content
        sections.append(BidDocumentSection(
            section_title=f"第三章附：包{pkg.package_id}分表响应",
            content=combined,
        ))

        # ── 独立的详细说明章节（关键性能+交付+培训） ──
        detail_content = _build_package_detail_section(
            pkg, tender, product, has_real_product, specs, evidence_refs, is_rich,
        )
        sections.append(BidDocumentSection(
            section_title=f"第三章附：包{pkg.package_id}详细说明",
            content=detail_content,
        ))

    return sections


def _build_package_detail_section(
    pkg: ProcurementPackage,
    tender: TenderDocument,
    product: Any,
    has_real_product: bool,
    specs: dict,
    evidence_refs: list,
    is_rich: bool,
) -> str:
    """构建单包详细说明（关键性能+交付+培训），与分表解耦。"""
    p_name = _as_text(getattr(product, "product_name", "")) if product else pkg.item_name
    p_model = _as_text(getattr(product, "model", "")) if product else ""
    p_mfr = _as_text(getattr(product, "manufacturer", "")) if product else ""
    p_brand = _as_text(getattr(product, "brand", "")) if product else ""

    content = f"### 包{pkg.package_id} 关键性能说明\n\n"
    if has_real_product:
        content += f"本包投标产品为 **{p_brand or p_mfr} {p_name}**（型号：{p_model}），生产厂家：{p_mfr}。\n\n"
    else:
        content += f"本包投标产品为{p_name}。{_rich_pending('补充品牌、型号及生产厂家信息')}\n\n"

    if specs:
        content += "**核心技术参数**：\n\n"
        for idx, (key, value) in enumerate(list(specs.items())[:10], start=1):
            content += f"{idx}. **{key}**：{_as_text(value)}。\n"
        content += "\n"
    else:
        content += f"{_rich_pending('补充关键性能参数')}\n\n"

    # 交付说明
    content += f"### 包{pkg.package_id} 交付说明\n\n"
    content += f"- 交货期：{_clean_delivery_text(pkg.delivery_time, '') or _rich_pending('按采购文件填写交货期限')}\n"
    content += f"- 交货地点：{_clean_delivery_text(pkg.delivery_place, '', is_place=True) or _rich_pending('按采购文件填写交货地点')}\n"
    content += f"- 交货方式：{_rich_pending('按采购文件或投标承诺填写')}\n\n"

    # 培训说明
    content += f"### 包{pkg.package_id} 使用与培训说明\n\n"
    content += f"- 培训对象：{pkg.item_name}操作人员、维护人员\n"
    content += f"- 培训方式：{_rich_pending('按培训方案填写')}\n"
    content += f"- 培训时长：{_rich_pending('按采购文件或投标承诺填写')}\n"
    warranty = tender.commercial_terms.warranty_period
    content += f"- 质保期：{warranty or _rich_pending('按采购文件或厂家承诺填写')}\n\n"

    return content
